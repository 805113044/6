import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from unittest import skipUnless
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from .forms import CourseImportForm
from .models import (
    AuditEvent,
    Booking,
    Room,
    TeachingRule,
    User,
    AcademicTerm,
    BorrowPeriod,
)
from .services import (
    create_booking,
    decide,
    cancel_booking,
    edit_booking,
    import_course_rows,
    verify_guest,
)
from .timetable import import_timetable, stop_rule, sync_timetables

PASSWORD = "This-booking-only!2026"


def seed_school(starts_on=None):
    today = timezone.localdate()
    start = starts_on or today - timedelta(days=today.weekday())
    term, _ = AcademicTerm.objects.update_or_create(
        pk=1,
        defaults={
            "name": "测试学期",
            "starts_on": start,
            "ends_on": start + timedelta(weeks=20),
            "first_week": 1,
            "booking_open": True,
        },
    )
    for n, (a, b) in enumerate(
        [
            (time(8), time(9, 40)),
            (time(10), time(11, 40)),
            (time(14), time(15, 40)),
            (time(16), time(17, 40)),
            (time(19), time(21)),
        ],
        1,
    ):
        BorrowPeriod.objects.update_or_create(number=n, defaults={"start": a, "end": b})
    return term


def interval(hour=10, offset=2):
    number = {8: 1, 10: 2, 12: 3, 14: 3, 15: 4, 16: 4, 19: 5}[hour]
    p = BorrowPeriod.objects.get(number=number)
    day = timezone.localdate() + timedelta(days=offset)
    return timezone.make_aware(datetime.combine(day, p.start)), timezone.make_aware(
        datetime.combine(day, p.end)
    )


def student(**kwargs):
    return {
        "student_name": "张同学",
        "student_id": "20260001",
        "student_department": "计算机一班",
        "student_contact": "student@example.test",
        "edit_password": PASSWORD,
        **kwargs,
    }


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_school()
        cls.admin = User.objects.create_superuser(
            username="administrator", password="admin-test-password!2026", email=""
        )
        cls.staff = User.objects.create_user(
            username="staff", password="staff-test-password!2026", is_staff=True
        )
        cls.normal = User.objects.create_user(
            username="legacy-teacher",
            password="ordinary-test-password!2026",
            role="teacher",
        )
        cls.room = Room.objects.create(
            name="A101", building="教学楼", capacity=60, floor=1
        )
        cls.lab = Room.objects.create(
            name="B201",
            building="实验楼",
            capacity=30,
            floor=2,
            room_type="lab",
            department="理学院",
        )

    def book(self, room=None, details=None, **overrides):
        start, end = interval()
        data = dict(
            title="学习研讨",
            start_at=start,
            end_at=end,
            participants=20,
            student=student() if details is None else details,
        )
        data.update(overrides)
        return create_booking(AnonymousUser(), (room or self.room).pk, **data)[0]

    def verify(self, client, booking, password=PASSWORD, identity="20260001"):
        return client.post(
            f"/bookings/{booking.pk}/verify/",
            {"student_id": identity, "password": password},
        )

    def edit_data(self, booking, **overrides):
        start, end = interval(14)
        return dict(
            room_id=booking.room_id,
            title="修改后用途",
            start_at=start,
            end_at=end,
            participants=5,
            student=student(),
            **overrides,
        )

    def test_guest_direct_booking_stores_only_password_hash(self):
        b = self.book()
        self.assertEqual(b.status, "approved")
        self.assertIsNone(b.applicant)
        self.assertTrue(check_password(PASSWORD, b.edit_password_hash))
        self.assertNotEqual(PASSWORD, b.edit_password_hash)
        self.assertIsNone(b.lookup_hash)
        self.assertEqual(b.events.count(), 1)

    def test_student_information_required(self):
        for details in (
            {},
            student(student_contact=""),
        ):
            with (
                self.subTest(details=list(details)),
                self.assertRaises(ValidationError),
            ):
                self.book(details=details)

    def test_only_admin_accounts_can_login(self):
        response = self.client.post(
            "/login/",
            {"username": "legacy-teacher", "password": "ordinary-test-password!2026"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.post(
                "/login/", {"username": "staff", "password": "staff-test-password!2026"}
            ).status_code,
            302,
        )
        start, end = interval()
        with self.assertRaises(PermissionDenied):
            create_booking(
                self.normal,
                self.room.pk,
                title="不允许普通账号",
                start_at=start,
                end_at=end,
                participants=1,
            )

    def test_public_names_but_not_personal_information(self):
        b = self.book()
        day = timezone.localdate(b.start_at)
        for path in (
            f"/rooms/{b.room_id}/?date={day}",
            f"/?date={day}",
            f"/schedule/?date={day}",
            f"/schedule/?view=day&date={day}",
            f"/bookings/{b.pk}/verify/",
        ):
            with self.subTest(path=path):
                response = Client().get(path)
                self.assertContains(response, "张同学")
                for private in (
                    "20260001",
                    "student@example.test",
                    "计算机一班",
                    "学习研讨",
                    b.edit_password_hash,
                    PASSWORD,
                ):
                    self.assertNotContains(response, private)

    def test_verification_only_grants_one_booking(self):
        first = self.book()
        second = self.book(
            start_at=interval(12)[0],
            end_at=interval(12)[1],
            details=student(edit_password="Another-booking!2026"),
        )
        self.assertEqual(self.verify(self.client, first).status_code, 302)
        self.assertContains(
            self.client.get(f"/bookings/{first.pk}/info/"), "student@example.test"
        )
        self.assertEqual(
            self.client.get(f"/bookings/{second.pk}/info/").status_code, 302
        )
        self.verify(self.client, second)
        self.assertNotEqual(
            self.client.session.get("booking_grant", {}).get("pk"), second.pk
        )
        self.verify(self.client, second, "Another-booking!2026")
        self.assertEqual(self.client.session["booking_grant"]["pk"], second.pk)

    def test_wrong_password_or_student_id_cannot_view(self):
        b = self.book()
        for identity, password in [
            ("wrong-id", PASSWORD),
            ("20260001", "wrong-password"),
        ]:
            response = self.verify(self.client, b, password, identity)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("booking_grant", self.client.session)
            self.assertNotContains(response, b.student_contact)
        self.assertFalse(verify_guest(None, "fake", PASSWORD))

    def test_grant_expires_after_fifteen_minutes(self):
        b = self.book()
        self.verify(self.client, b)
        grant = self.client.session["booking_grant"]
        with patch(
            "booking.services.timezone.now",
            return_value=timezone.now() + timedelta(minutes=16),
        ):
            self.assertEqual(
                self.client.get(f"/bookings/{b.pk}/info/").status_code, 302
            )
        self.assertEqual(grant["pk"], b.pk)

    def test_owner_edit_and_resubmit_returned_record(self):
        b = self.book()
        decide(self.admin, b.pk, "returned", "请修改人数")
        b.refresh_from_db()
        self.assertEqual(b.status, "returned")
        updated = edit_booking(
            AnonymousUser(),
            b.pk,
            guest_authorized=True,
            expected_version=b.credential_version,
            **self.edit_data(b),
        )
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.title, "修改后用途")
        self.assertEqual(updated.credential_version, 2)

    def test_editing_other_record_is_forbidden(self):
        b = self.book()
        with self.assertRaises(PermissionDenied):
            edit_booking(AnonymousUser(), b.pk, **self.edit_data(b))
        self.assertEqual(
            self.client.post(f"/bookings/{b.pk}/edit/", {}).status_code, 302
        )
        self.assertEqual(self.client.post(f"/bookings/{b.pk}/cancel/").status_code, 403)

    def test_stale_grant_cannot_edit_or_cancel(self):
        b = self.book()
        edit_booking(self.admin, b.pk, reason="核实变更", **self.edit_data(b))
        with self.assertRaises(PermissionDenied):
            cancel_booking(
                AnonymousUser(), b.pk, guest_authorized=True, expected_version=1
            )
        with self.assertRaises(PermissionDenied):
            edit_booking(
                AnonymousUser(),
                b.pk,
                guest_authorized=True,
                expected_version=1,
                **self.edit_data(b),
            )

    def test_password_change_revokes_previous_sessions(self):
        b = self.book()
        first, second = Client(), Client()
        self.verify(first, b)
        self.verify(second, b)
        new_info = student(
            edit_password="New-private-booking!2026", student_id="NEW-ID"
        )
        args = self.edit_data(b)
        args["student"] = new_info
        edit_booking(
            AnonymousUser(), b.pk, guest_authorized=True, expected_version=1, **args
        )
        self.assertEqual(first.get(f"/bookings/{b.pk}/info/").status_code, 302)
        self.assertEqual(second.get(f"/bookings/{b.pk}/info/").status_code, 302)
        b.refresh_from_db()
        self.assertFalse(verify_guest(b, "20260001", PASSWORD))
        self.assertTrue(verify_guest(b, "NEW-ID", "New-private-booking!2026"))

    def test_edit_conflict_rolls_back_original_booking(self):
        b = self.book()
        other = self.book(
            room=self.lab, start_at=interval(14)[0], end_at=interval(14)[1]
        )
        args = self.edit_data(b)
        args["room_id"] = self.lab.pk
        with self.assertRaises(ValidationError):
            edit_booking(
                AnonymousUser(), b.pk, guest_authorized=True, expected_version=1, **args
            )
        b.refresh_from_db()
        self.assertEqual(b.room_id, self.room.pk)
        self.assertEqual(b.start_at, interval()[0])
        self.assertEqual(other.status, "approved")

    def test_first_submission_wins_and_adjacent_allowed(self):
        self.book()
        start, end = interval()
        with self.assertRaises(ValidationError):
            self.book(start_at=start + timedelta(minutes=10))
        self.book(start_at=interval(14)[0], end_at=interval(14)[1])
        self.book(room=self.lab)

    def test_capacity_hours_and_duration(self):
        start, end = interval()
        for args in (
            {"participants": 61},
            {"participants": 0},
            {"end_at": start},
            {"end_at": start + timedelta(hours=5)},
            {"start_at": start - timedelta(days=40)},
        ):
            with self.subTest(args=args), self.assertRaises(ValidationError):
                self.book(**args)
        self.room.is_active = False
        self.room.save()
        with self.assertRaises(ValidationError):
            self.book()

    def test_admin_return_requires_reason_and_releases_time(self):
        b = self.book()
        with self.assertRaises(ValidationError):
            decide(self.admin, b.pk, "returned")
        decide(self.admin, b.pk, "returned", "用途需确认")
        second = self.book()
        self.assertEqual(second.status, "approved")
        with self.assertRaises(ValidationError):
            edit_booking(
                AnonymousUser(),
                b.pk,
                guest_authorized=True,
                expected_version=1,
                room_id=b.room_id,
                title=b.title,
                start_at=b.start_at,
                end_at=b.end_at,
                participants=1,
                student=student(),
            )

    def test_admin_can_edit_and_view_all_records(self):
        b = self.book()
        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(f"/bookings/{b.pk}/info/"), b.student_contact
        )
        self.assertContains(self.client.get("/review/"), b.student_id)
        args = self.edit_data(b)
        with self.assertRaises(ValidationError):
            edit_booking(self.staff, b.pk, **args)
        updated = edit_booking(self.staff, b.pk, reason="教务调整", **args)
        self.assertEqual(updated.title, "修改后用途")
        self.assertTrue(updated.events.filter(action="管理员编辑").exists())

    def test_cancel_requires_ownership_and_not_ended(self):
        b = self.book()
        cancel_booking(AnonymousUser(), b.pk, guest_authorized=True, expected_version=1)
        b.refresh_from_db()
        self.assertEqual(b.status, "cancelled")
        b = self.book()
        b.start_at = timezone.now() - timedelta(hours=2)
        b.end_at = timezone.now() - timedelta(minutes=1)
        b.save()
        with self.assertRaises(ValidationError):
            cancel_booking(
                AnonymousUser(), b.pk, guest_authorized=True, expected_version=1
            )
        cancel_booking(self.admin, b.pk, "教务撤销")

    def test_guest_browser_form_and_lookup(self):
        start, _ = interval()
        data = dict(
            title="浏览器测试",
            date=start.date(),
            period=BorrowPeriod.objects.get(number=2).pk,
            participants=5,
            **student(),
            edit_password_confirm=PASSWORD,
            consent="on",
        )
        response = self.client.post(f"/rooms/{self.room.pk}/", data)
        self.assertEqual(response.status_code, 302)
        b = Booking.objects.get()
        self.assertContains(self.client.get("/receipt/"), "已批准")
        other = Client()
        self.assertEqual(
            other.post(
                "/lookup/",
                {"booking_id": b.pk, "student_id": b.student_id, "password": PASSWORD},
            ).status_code,
            302,
        )
        self.assertContains(other.get("/receipt/"), b.student_contact)
        self.assertEqual(other.post(f"/bookings/{b.pk}/cancel/").status_code, 302)

    def test_owner_edit_http_form(self):
        b = self.book()
        self.verify(self.client, b)
        self.assertContains(
            self.client.get(f"/bookings/{b.pk}/edit/"),
            f'value="{timezone.localdate(b.start_at).isoformat()}"',
        )
        start, end = interval(15)
        data = dict(
            title="修改活动",
            room=self.lab.pk,
            date=start.date(),
            period=BorrowPeriod.objects.get(number=4).pk,
            participants=4,
            **student(),
        )
        response = self.client.post(f"/bookings/{b.pk}/edit/", data)
        self.assertEqual(response.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.room_id, self.lab.pk)
        self.assertContains(self.client.get(f"/bookings/{b.pk}/info/"), "修改活动")

    def test_filters_and_daily_weekly_views(self):
        b = self.book()
        response = self.client.get("/?floor=2&room_type=lab")
        self.assertEqual([r.pk for r in response.context["page"]], [self.lab.pk])
        day = timezone.localdate(b.start_at)
        response = self.client.get(
            f"/?date={day}&period={BorrowPeriod.objects.get(number=2).pk}"
        )
        self.assertNotIn(self.room.pk, [r.pk for r in response.context["page"]])
        week = self.client.get(f"/schedule/?date={day}")
        self.assertEqual(len(week.context["columns"]), 7)
        self.assertEqual(len(week.context["rows"]), 5)
        self.assertEqual(len(week.context["rows"][0]["cells"]), 7)
        self.assertContains(week, 'scope="row"', count=5)
        self.assertContains(week, b.applicant_label)
        self.assertContains(week, self.room.name)
        self.assertContains(week, self.lab.name)
        day_view = self.client.get(f"/schedule/?date={day}&view=day")
        self.assertEqual(len(day_view.context["columns"]), 1)
        self.assertEqual(len(day_view.context["rows"]), 5)

    def test_schedule_shows_all_filtered_rooms_in_each_period(self):
        extra = [
            Room.objects.create(name=f"C{number:03}", building="新楼", floor=3)
            for number in range(12)
        ]
        response = self.client.get("/schedule/?view=day&floor=3")
        self.assertEqual(response.context["room_count"], 12)
        self.assertEqual(len(response.context["rows"]), 5)
        for room in extra:
            self.assertContains(response, room.name, count=5)

    def test_retention_cascades_records_and_sessions(self):
        b = self.book()
        expiry = timezone.now()-timedelta(days=settings.BOOKING_RETENTION_DAYS, seconds=1)
        Booking.objects.filter(pk=b.pk).update(
            start_at=expiry-timedelta(hours=2), end_at=expiry-timedelta(hours=1),
            created_at=expiry-timedelta(days=100),
        )
        self.assertFalse(Booking.objects.retained().exists())
        self.assertEqual(self.client.get(f"/bookings/{b.pk}/verify/").status_code, 404)
        Session.objects.create(
            session_key="expired",
            session_data="",
            expire_date=timezone.now() - timedelta(hours=1),
        )
        call_command("purge_expired", stdout=io.StringIO())
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertFalse(Session.objects.filter(pk="expired").exists())

    def test_reports_private_data_and_csv_formula_escaping(self):
        from .views import safe_csv

        b = self.book()
        self.client.force_login(self.admin)
        response = self.client.get("/reports/?export=csv")
        for value in (b.student_id, b.student_contact, b.student_name):
            self.assertNotIn(value, response.content.decode())
        day = timezone.localdate(b.start_at)
        self.assertEqual(
            self.client.get(f"/reports/?from={day}&to={day}").context["total"], 1
        )
        self.assertEqual(
            self.client.get(f"/reports/?from={day + timedelta(days=1)}").context[
                "total"
            ],
            0,
        )
        self.assertEqual(safe_csv(" =1+1"), "' =1+1")

    def test_csrf_rate_limit_and_private_headers(self):
        b = self.book()
        self.assertEqual(
            Client(enforce_csrf_checks=True)
            .post(f"/bookings/{b.pk}/cancel/")
            .status_code,
            403,
        )
        self.assertEqual(self.client.get(f"/bookings/{b.pk}/cancel/").status_code, 405)
        for _ in range(15):
            self.verify(self.client, b, "wrong")
        self.verify(self.client, b)
        self.assertNotIn("booking_grant", self.client.session)
        response = self.client.get("/lookup/")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])

    def test_admin_and_public_pages(self):
        for path in (
            "/",
            "/schedule/",
            "/login/",
            "/lookup/",
            "/healthz/",
            f"/rooms/{self.room.pk}/",
        ):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_end_verification_revokes_access(self):
        b = self.book()
        self.verify(self.client, b)
        self.client.post("/forget/")
        self.assertEqual(self.client.get(f"/bookings/{b.pk}/info/").status_code, 302)
        self.client.force_login(self.admin)
        for path in (
            "/review/",
            "/courses/import/",
            "/courses/template/",
            "/courses/template/?kind=semester&format=xlsx",
            "/reports/",
            "/admin/",
            "/admin/booking/user/add/",
        ):
            self.assertEqual(self.client.get(path).status_code, 200)


class TimetableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        WorkflowTests.setUpTestData.__func__(cls)
        seed_school(
            timezone.localdate() + timedelta(days=7 - timezone.localdate().weekday())
        )

    book = WorkflowTests.book

    def rule_row(self, **changes):
        monday = timezone.localdate() + timedelta(
            days=7 - timezone.localdate().weekday()
        )
        rule = dict(
            title="高等数学",
            teacher="李老师",
            term_start=monday,
            week_start=1,
            week_end=18,
            week_pattern="odd",
            weekday=3,
            start=time(8),
            end=time(9, 40),
            excluded_dates=[],
        )
        rule.update(changes)
        return {"room": self.room.name, "rule": rule}

    def test_odd_even_and_excluded_dates(self):
        row = self.rule_row()
        first = row["rule"]["term_start"] + timedelta(days=2)
        import_timetable(self.admin, [row], dry_run=False)
        days = [
            timezone.localdate(b.start_at)
            for b in Booking.objects.filter(kind="course")
        ]
        self.assertIn(first, days)
        self.assertNotIn(first + timedelta(days=7), days)
        self.assertContains(
            self.client.get(f"/schedule/?date={first + timedelta(days=14)}"), "李老师"
        )
        even = self.rule_row(week_pattern="even")
        import_timetable(self.admin, [even], dry_run=False)
        self.assertTrue(
            TeachingRule.objects.get(week_pattern="even").occurs_on(
                first + timedelta(days=7)
            )
        )
        rule = TeachingRule.objects.get(week_pattern="odd")
        rule.excluded_dates = [first.isoformat()]
        self.assertFalse(rule.occurs_on(first))

    def test_import_excluded_date_has_no_occupancy(self):
        row = self.rule_row()
        first = row["rule"]["term_start"] + timedelta(days=2)
        row["rule"]["excluded_dates"] = [first.isoformat()]
        import_timetable(self.admin, [row], dry_run=False)
        start = timezone.make_aware(datetime.combine(first, time(8)))
        self.assertFalse(Booking.objects.filter(start_at=start).exists())
        self.book(
            start_at=start,
            end_at=timezone.make_aware(
                datetime.combine(timezone.localdate(start), time(9, 40))
            ),
        )

    def test_semester_preview_idempotent_and_rule_collision(self):
        row = self.rule_row()
        self.assertEqual(import_timetable(self.admin, [row], dry_run=True), (1, 0))
        self.assertEqual(TeachingRule.objects.count(), 0)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(import_timetable(self.admin, [row], dry_run=False), (1, 0))
        self.assertEqual(import_timetable(self.admin, [row], dry_run=False), (0, 1))
        with self.assertRaises(ValidationError):
            import_timetable(
                self.admin, [self.rule_row(title="重叠课程")], dry_run=False
            )
        self.assertEqual(TeachingRule.objects.count(), 1)

    def test_unmaterialized_rules_block_first_booking(self):
        row = self.rule_row()
        import_timetable(self.admin, [row], dry_run=False)
        Booking.objects.all().delete()
        first = row["rule"]["term_start"] + timedelta(days=2)
        start = timezone.make_aware(datetime.combine(first, time(8)))
        with self.assertRaises(ValidationError):
            self.book(
                start_at=start,
                end_at=timezone.make_aware(
                    datetime.combine(timezone.localdate(start), time(9, 40))
                ),
            )

    def test_stopping_rule_releases_future_and_does_not_regenerate(self):
        import_timetable(self.admin, [self.rule_row()], dry_run=False)
        rule = TeachingRule.objects.get()
        stop_rule(self.admin, rule.pk, "调课")
        sync_timetables()
        self.assertFalse(Booking.objects.occupying().exists())
        self.assertTrue(Booking.objects.filter(status="cancelled").exists())

    def test_edited_occurrence_is_not_regenerated(self):
        import_timetable(self.admin, [self.rule_row()], dry_run=False)
        b = Booking.objects.order_by("start_at").first()
        edit_booking(
            self.admin,
            b.pk,
            room_id=b.room_id,
            title="课程调整",
            start_at=b.start_at + timedelta(hours=3),
            end_at=b.end_at + timedelta(hours=3),
            participants=1,
            reason="调课",
        )
        count = Booking.objects.count()
        sync_timetables()
        self.assertEqual(Booking.objects.count(), count)

    def test_rule_survives_reservation_retention_and_rolls_forward(self):
        import_timetable(self.admin, [self.rule_row(week_pattern="all")], dry_run=False)
        future = timezone.now() + timedelta(days=65)
        with patch("django.utils.timezone.now", return_value=future):
            call_command("purge_expired", stdout=io.StringIO())
            sync_timetables()
            self.assertEqual(TeachingRule.objects.count(), 1)
            self.assertTrue(Booking.objects.filter(start_at__gt=future).exists())
            self.assertFalse(
                Booking.objects.filter(
                    created_at__lte=future - timedelta(days=settings.BOOKING_RETENTION_DAYS)
                ).exists()
            )

    def test_date_import_preview_atomicity_and_duplicate(self):
        start, end = interval(offset=9)
        row = dict(
            room=self.room.name,
            title="课程",
            start_at=start,
            end_at=end,
            teacher="王老师",
        )
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=True), (1, 0))
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=False), (1, 0))
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=False), (0, 1))
        with self.assertRaises(ValidationError):
            import_course_rows(
                self.admin, [{**row, "title": "冲突课程"}], dry_run=False
            )
        self.assertEqual(Booking.objects.count(), 1)

    def test_semester_csv_and_excel_parsing(self):
        from openpyxl import Workbook

        monday = self.rule_row()["rule"]["term_start"].isoformat()
        header = [
            "room",
            "title",
            "teacher",
            "term_start",
            "week_start",
            "week_end",
            "week_pattern",
            "weekday",
            "start",
            "end",
            "excluded_dates",
        ]
        row = ["A101", "课程", "教师", monday, 1, 18, "单周", 3, "08:00", "09:40", ""]
        import csv

        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(header)
        writer.writerow(row)
        form = CourseImportForm(
            {}, {"file": SimpleUploadedFile("t.csv", text.getvalue().encode())}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["file"][0]["rule"]["week_pattern"], "odd")
        wb = Workbook()
        ws = wb.active
        ws.append(header)
        ws.append(row)
        output = io.BytesIO()
        wb.save(output)
        form = CourseImportForm(
            {}, {"file": SimpleUploadedFile("t.xlsx", output.getvalue())}
        )
        self.assertTrue(form.is_valid(), form.errors)
        broken = CourseImportForm(
            {}, {"file": SimpleUploadedFile("bad.xlsx", b"not a workbook")}
        )
        self.assertFalse(broken.is_valid())

    def test_bad_weekday_and_missing_columns_rejected(self):
        monday = self.rule_row()["rule"]["term_start"].isoformat()
        for text in (
            f"room,title,teacher,term_start,week_start,week_end,week_pattern,weekday,start,end,excluded_dates\nA101,C,T,{monday},1,18,odd,9,08:00,09:00,\n",
            "room,title,date,start,end\nA101,Oops,2026-01-01,08:00\n",
        ):
            form = CourseImportForm(
                {}, {"file": SimpleUploadedFile("t.csv", text.encode())}
            )
            self.assertFalse(form.is_valid())

    def test_later_semester_week_is_visible_without_retaining_booking_rows(self):
        row = self.rule_row(week_pattern="all")
        import_timetable(self.admin, [row], dry_run=False)
        day = row["rule"]["term_start"] + timedelta(weeks=10, days=2)
        self.assertFalse(
            Booking.objects.filter(
                start_at__gte=timezone.make_aware(datetime.combine(day, time.min))
            ).exists()
        )
        self.assertContains(self.client.get(f"/schedule/?date={day}"), "李老师")
        self.assertContains(
            self.client.get(f"/rooms/{self.room.pk}/?date={day}"), "李老师"
        )


@skipUnless(
    connection.vendor in ("mysql", "postgresql"),
    "生产数据库上验证行级锁；SQLite 不支持",
)
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        seed_school()

    def test_move_and_new_booking_cannot_claim_same_slot(self):
        room_a = Room.objects.create(name="MOVE-A", building="教学楼", capacity=20)
        room_b = Room.objects.create(name="MOVE-B", building="教学楼", capacity=20)
        start, end = interval()
        original, _ = create_booking(
            AnonymousUser(),
            room_a.pk,
            title="原预约",
            start_at=start,
            end_at=end,
            participants=2,
            student=student(),
        )
        gate = threading.Barrier(2)

        def act(action):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                if action == "move":
                    edit_booking(
                        AnonymousUser(),
                        original.pk,
                        room_id=room_b.pk,
                        title="换教室",
                        start_at=start,
                        end_at=end,
                        participants=2,
                        student=student(),
                        guest_authorized=True,
                        expected_version=1,
                    )
                else:
                    create_booking(
                        AnonymousUser(),
                        room_b.pk,
                        title="新预约",
                        start_at=start,
                        end_at=end,
                        participants=2,
                        student=student(),
                    )
                return "success"
            except ValidationError:
                return "conflict"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(act, ["move", "new"]))
        self.assertCountEqual(results, ["success", "conflict"])
        self.assertEqual(Booking.objects.occupying().filter(room=room_b).count(), 1)

    def test_simultaneous_guest_submissions_only_one_wins(self):
        room = Room.objects.create(name="R1", building="教学楼", capacity=20)
        start, end = interval()
        gate = threading.Barrier(2)

        def submit(_):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                create_booking(
                    AnonymousUser(),
                    room.pk,
                    title="并发预约",
                    start_at=start,
                    end_at=end,
                    participants=2,
                    student=student(),
                )
                return "created"
            except ValidationError:
                return "conflict"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(submit, range(2)))
        self.assertCountEqual(result, ["created", "conflict"])
        self.assertEqual(Booking.objects.count(), 1)
