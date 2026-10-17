from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from .models import Booking, BorrowPeriod, User
from .school import availability
from .services import cancel_booking, create_booking, edit_booking
from . import tests as workflow
from .tests import PASSWORD, interval, student


class LocalPlatformTests(TestCase):
    setUpTestData = classmethod(workflow.WorkflowTests.setUpTestData.__func__)
    book = workflow.WorkflowTests.book
    edit_data = workflow.WorkflowTests.edit_data
    verify = workflow.WorkflowTests.verify

    def test_lookup_right_panel_needs_fresh_credentials_on_every_visit(self):
        booking = self.book()
        # Prior per-booking session grants never make a fresh GET show results.
        self.verify(self.client, booking)
        page = self.client.get("/lookup/")
        self.assertEqual(list(page.context["bookings"]), [])
        self.assertContains(page, "先验证，再查看")
        good = self.client.post("/lookup/", {
            "student_id": booking.student_id, "password": PASSWORD})
        self.assertEqual(good.status_code, 302)
        shown = self.client.get("/lookup/")
        self.assertEqual([row.pk for row in shown.context["bookings"]], [booking.pk])
        # The one-time marker has been consumed; the old session grant is irrelevant.
        self.assertEqual(list(self.client.get("/lookup/").context["bookings"]), [])
        # Invalid verification must never reveal previous results.
        invalid = self.client.post("/lookup/", {
            "student_id": booking.student_id, "password": "not-the-password"}, follow=True)
        self.assertEqual(list(invalid.context["bookings"]), [])
        # Clicking into the just verified booking still respects its 15-minute grant.
        self.assertEqual(self.client.get(f"/bookings/{booking.pk}/info/").status_code, 200)

    def test_credential_pair_lists_only_matching_records_and_revokes_per_record(self):
        first = self.book()
        second = self.book(start_at=interval(14)[0], end_at=interval(14)[1])
        other_password = self.book(
            start_at=interval(16)[0],
            end_at=interval(16)[1],
            details=student(edit_password="Another-private!2026"),
        )
        other_id = self.book(
            start_at=interval(19)[0],
            end_at=interval(19)[1],
            details=student(student_id="OTHER-ID"),
        )
        response = self.client.post(
            "/lookup/", {"student_id": "20260001", "password": PASSWORD}, follow=True
        )
        self.assertEqual(
            {b.pk for b in response.context["bookings"]}, {first.pk, second.pk}
        )
        self.assertNotIn(PASSWORD, str(self.client.session.items()))
        for booking in (first, second):
            self.assertEqual(
                self.client.get(f"/bookings/{booking.pk}/info/").status_code, 200
            )
        for booking in (other_password, other_id):
            self.assertEqual(
                self.client.get(f"/bookings/{booking.pk}/info/").status_code, 302
            )
        data = self.edit_data(first)
        data.update(start_at=interval(8)[0], end_at=interval(8)[1])
        edit_booking(self.admin, first.pk, reason="资料核实", **data)
        self.assertEqual(
            self.client.get(f"/bookings/{first.pk}/info/").status_code, 302
        )
        self.assertEqual(
            self.client.get(f"/bookings/{second.pk}/info/").status_code, 200
        )
        self.client.post("/forget/")
        self.assertFalse(self.client.get("/lookup/").context["bookings"])
        self.assertEqual(
            self.client.get(f"/bookings/{second.pk}/info/").status_code, 302
        )

    def test_wrong_pair_expiry_and_legacy_hash_lookup(self):
        booking = self.book()
        Booking.objects.filter(pk=booking.pk).update(credential_tag="")
        response = self.client.post(
            "/lookup/", {"student_id": "20260001", "password": "incorrect"}, follow=True
        )
        self.assertFalse(response.context["bookings"])
        response = self.client.post(
            "/lookup/", {"student_id": "20260001", "password": PASSWORD}, follow=True
        )
        self.assertEqual([b.pk for b in response.context["bookings"]], [booking.pk])
        booking.refresh_from_db()
        self.assertTrue(booking.credential_tag)
        with patch(
            "booking.services.timezone.now",
            return_value=timezone.now() + timedelta(minutes=16),
        ):
            self.assertFalse(self.client.get("/lookup/").context["bookings"])

    def test_password_and_id_changes_update_pair_search(self):
        from .services import lookup_guest_bookings

        booking = self.book()
        args = self.edit_data(booking)
        args["student"] = student(
            student_id="新工号", edit_password="New-password!2026"
        )
        edit_booking(self.admin, booking.pk, reason="修改信息", **args)
        self.assertFalse(lookup_guest_bookings("20260001", PASSWORD))
        self.assertEqual(
            [b.pk for b in lookup_guest_bookings("新工号", "New-password!2026")],
            [booking.pk],
        )
        args["student"] = student(student_id="新工号2", edit_password="")
        edit_booking(self.admin, booking.pk, reason="修改工号", **args)
        self.assertEqual(
            [b.pk for b in lookup_guest_bookings("新工号2", "New-password!2026")],
            [booking.pk],
        )

    def test_ongoing_period_available_and_can_borrow_edit_cancel_immediately(self):
        start, end = interval(10, offset=0)
        now = start + timedelta(minutes=20)
        with patch("booking.services.timezone.now", return_value=now):
            cells = availability(
                [self.room],
                timezone.localtime(start).date(),
                list(BorrowPeriod.objects.all()),
            )[self.room.pk]
            self.assertTrue(cells[1]["free"])
            self.assertTrue(cells[1]["ongoing"])
            booking = self.book(start_at=start, end_at=end)
            self.assertFalse(
                availability(
                    [self.room],
                    timezone.localtime(start).date(),
                    list(BorrowPeriod.objects.all()),
                )[self.room.pk][1]["free"]
            )
            with self.assertRaises(ValidationError):
                self.book(start_at=start, end_at=end)
            edit_booking(
                AnonymousUser(),
                booking.pk,
                room_id=self.room.pk,
                title="临时使用调整",
                start_at=start,
                end_at=end,
                participants=2,
                guest_authorized=True,
                expected_version=1,
            )
            cancel_booking(
                AnonymousUser(), booking.pk, guest_authorized=True, expected_version=1
            )
            self.assertTrue(
                availability(
                    [self.room],
                    timezone.localtime(start).date(),
                    list(BorrowPeriod.objects.all()),
                )[self.room.pk][1]["free"]
            )
        with patch("booking.services.timezone.now", return_value=end):
            with self.assertRaises(ValidationError):
                self.book(start_at=start, end_at=end)
            self.assertFalse(
                availability(
                    [self.room],
                    timezone.localtime(start).date(),
                    list(BorrowPeriod.objects.all()),
                )[self.room.pk][1]["free"]
            )

    def test_ongoing_course_still_blocks_instant_borrow(self):
        start, end = interval(10, offset=0)
        now = start + timedelta(minutes=20)
        Booking.objects.create(
            room=self.room,
            kind="course",
            title="课堂",
            start_at=start,
            end_at=end,
            status="approved",
        )
        with patch("booking.services.timezone.now", return_value=now):
            with self.assertRaises(ValidationError):
                self.book(start_at=start, end_at=end)

    def test_unified_management_and_multiple_admin_accounts(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/manage/administrators/new/",
            {
                "username": "new-manager",
                "display_name": "新管理员",
                "department": "教务处",
                "password1": "Independent-admin!2026",
                "password2": "Independent-admin!2026",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        manager = User.objects.get(username="new-manager")
        self.assertTrue(manager.is_staff)
        self.assertFalse(manager.is_superuser)
        another = Client()
        self.assertTrue(
            another.login(username="new-manager", password="Independent-admin!2026")
        )
        for path in (
            "/manage/",
            "/review/",
            "/manage/school/",
            "/manage/rooms/",
            "/courses/import/",
            "/reports/",
        ):
            self.assertEqual(another.get(path).status_code, 200)
        for path in (
            "/manage/administrators/",
            "/manage/administrators/new/",
            f"/manage/administrators/{self.staff.pk}/",
        ):
            self.assertEqual(another.get(path).status_code, 403)
        self.client.post(
            f"/manage/administrators/{manager.pk}/",
            {
                "display_name": "更新管理员",
                "department": "教务处",
                "is_active": "on",
                "password1": "Replacement-admin!2026",
                "password2": "Replacement-admin!2026",
            },
        )
        self.assertEqual(another.get("/manage/").status_code, 302)
        self.assertFalse(
            another.login(username="new-manager", password="Independent-admin!2026")
        )
        self.assertTrue(
            another.login(username="new-manager", password="Replacement-admin!2026")
        )
        self.client.post(
            f"/manage/administrators/{manager.pk}/",
            {"display_name": "更新管理员", "department": "教务处"},
        )
        self.assertEqual(another.get("/manage/").status_code, 302)
        self.assertFalse(
            another.login(username="new-manager", password="Replacement-admin!2026")
        )
        self.assertEqual(
            self.client.post(
                f"/manage/administrators/{self.admin.pk}/", {}
            ).status_code,
            403,
        )
        self.assertEqual(Client().get("/manage/").status_code, 302)

    def test_shared_scroll_table_local_assets_and_brand(self):
        response = self.client.get("/schedule/")
        self.assertContains(response, "教室借用平台")
        self.assertContains(response, "schedule-scroll")
        self.assertContains(response, "08:00–09:40")
        self.assertNotContains(response, "mobile-calendar")
        self.assertNotContains(response, "https://")
        self.assertNotContains(response, "知室")
