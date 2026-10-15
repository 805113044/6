import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from unittest import skipUnless
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from .forms import CourseImportForm
from .models import AuditEvent, Booking, Room, User
from .services import create_booking, decide, cancel_booking, import_course_rows


def interval(hour=10):
    day = timezone.localdate() + timedelta(days=2)
    start = timezone.make_aware(datetime.combine(day, time(hour)))
    return start, start + timedelta(hours=1)


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.teacher = User.objects.create_user(
            username="teacher",
            password="test-only-long-password",
            role="teacher",
            department="计算机学院",
        )
        cls.other = User.objects.create_user(
            username="other",
            password="test-only-long-password",
            role="teacher",
            department="外语学院",
        )
        cls.admin = User.objects.create_superuser(
            username="administrator", password="test-only-admin-password", email=""
        )
        cls.manager = User.objects.create_user(
            username="manager",
            password="test-only-long-password",
            is_staff=True,
            department="计算机学院",
        )
        cls.room = Room.objects.create(name="A101", building="教学楼", capacity=60)
        cls.private = Room.objects.create(
            name="B101", building="教学楼", capacity=30, department="计算机学院"
        )

    def book(self, user=None, room=None, **overrides):
        start, end = interval()
        args = {
            "title": "学习研讨",
            "start_at": start,
            "end_at": end,
            "participants": 20,
        }
        args.update(overrides)
        user = user or self.teacher
        return create_booking(user, (room or self.room).pk, **args)

    def guest(self):
        return self.book(
            AnonymousUser(),
            student={
                "student_name": "张同学",
                "student_id": "20260001",
                "student_department": "计算机一班",
                "student_contact": "student@example.test",
            },
        )

    def test_teacher_auto_approved(self):
        b, code = self.book()
        self.assertEqual(b.status, "approved")
        self.assertFalse(code)
        self.assertEqual(b.events.count(), 1)

    def test_guest_pending_and_hashed_lookup(self):
        b, code = self.guest()
        self.assertIsNone(b.applicant)
        self.assertEqual(b.status, "pending")
        self.assertEqual(b.lookup_hash, hashlib.sha256(code.encode()).hexdigest())
        self.assertNotEqual(b.lookup_hash, code)
        self.assertGreaterEqual(len(code), 40)
        c = Client()
        self.assertRedirects(c.post("/lookup/", {"code": code}), "/receipt/")
        receipt = c.get("/receipt/")
        self.assertContains(receipt, "张同学")
        self.assertContains(receipt, "student@example.test")
        self.assertEqual(Client().get("/receipt/").status_code, 404)

    def test_guest_validation_requires_student_information(self):
        with self.assertRaises(ValidationError):
            self.book(AnonymousUser())

    def test_guest_post_end_to_end_receipt_and_cancellation(self):
        start, end = interval()
        response = self.client.post(
            f"/rooms/{self.room.pk}/",
            {
                "title": "小组活动",
                "date": start.date(),
                "start": "10:00",
                "end": "11:00",
                "participants": 5,
                "student_name": "李同学",
                "student_id": "00002",
                "student_department": "一班",
                "student_contact": "13800000000",
                "consent": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        b = Booking.objects.get()
        page = self.client.get("/receipt/")
        code = page.context["code"]
        self.assertTrue(code)
        self.assertEqual(self.client.get("/receipt/").context["code"], "")
        self.assertEqual(self.client.post(f"/bookings/{b.pk}/cancel/").status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.status, "cancelled")

    def test_student_approval_rejection_and_state_transition(self):
        b, _ = self.guest()
        with self.assertRaises(ValidationError):
            decide(self.admin, b.pk, "rejected")
        decide(self.admin, b.pk, "approved", "核实通过")
        b.refresh_from_db()
        self.assertEqual(b.status, "approved")
        with self.assertRaises(ValidationError):
            decide(self.admin, b.pk, "rejected", "重复处理")

    def test_pending_and_approved_block_overlap(self):
        self.guest()
        start, end = interval()
        with self.assertRaises(ValidationError):
            self.book(
                start_at=start + timedelta(minutes=30),
                end_at=end + timedelta(minutes=30),
            )
        # Adjacent bookings and another room remain usable.
        self.book(start_at=end, end_at=end + timedelta(hours=1))
        self.book(room=self.private)

    def test_inactive_room_capacity_duration_and_hours(self):
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

    def test_department_permissions_and_admin_boundary(self):
        with self.assertRaises(PermissionDenied):
            self.book(self.other, room=self.private)
        self.assertEqual(self.client.get(f"/rooms/{self.private.pk}/").status_code, 404)
        b, _ = self.guest()
        with self.assertRaises(PermissionDenied):
            decide(self.manager, b.pk, "approved")
        self.client.force_login(self.manager)
        self.assertRedirects(
            self.client.get("/admin/"),
            "/admin/login/?next=/admin/",
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.post(f"/review/{b.pk}/", {"decision": "approved"}).status_code,
            404,
        )

    def test_private_student_data_not_in_public_schedule(self):
        b, _ = self.guest()
        response = self.client.get(
            f"/rooms/{self.room.pk}/?date={timezone.localdate(b.start_at).isoformat()}"
        )
        for secret in (
            b.student_name,
            b.student_id,
            b.student_contact,
            b.title,
            b.lookup_hash,
        ):
            self.assertNotContains(response, secret)
        self.assertContains(response, "申请审批中")

    def test_unauthorized_cancellation_and_csrf(self):
        b, _ = self.guest()
        self.assertEqual(Client().post(f"/bookings/{b.pk}/cancel/").status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf.post(f"/bookings/{b.pk}/cancel/").status_code, 403)
        self.assertEqual(self.client.get(f"/bookings/{b.pk}/cancel/").status_code, 405)

    def test_owner_cancel_and_other_user_forbidden(self):
        b, _ = self.book()
        with self.assertRaises(PermissionDenied):
            cancel_booking(self.other, b.pk)
        cancel_booking(self.teacher, b.pk)
        b.refresh_from_db()
        self.assertEqual(b.status, "cancelled")
        self.book()

    def test_cancellation_deadline_and_admin_override(self):
        b, _ = self.book()
        b.start_at = timezone.now() + timedelta(minutes=10)
        b.save()
        with self.assertRaises(ValidationError):
            cancel_booking(self.teacher, b.pk)
        with self.assertRaises(ValidationError):
            cancel_booking(self.admin, b.pk)
        cancel_booking(self.admin, b.pk, "教学临时调整")

    def course_row(self):
        start, end = interval()
        return {"room": "A101", "title": "高等数学", "start_at": start, "end_at": end}

    def test_course_preview_atomic_import_and_idempotency(self):
        row = self.course_row()
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=True), (1, 0))
        self.assertFalse(Booking.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=False), (1, 0))
        self.assertEqual(import_course_rows(self.admin, [row], dry_run=False), (0, 1))
        with self.assertRaises(ValidationError):
            self.book()

    def test_import_conflict_rolls_back_whole_batch(self):
        row = self.course_row()
        other = {
            **row,
            "title": "重叠课程",
            "start_at": row["start_at"] + timedelta(minutes=10),
        }
        with self.assertRaises(ValidationError):
            import_course_rows(self.admin, [row, other], dry_run=False)
        self.assertEqual(Booking.objects.count(), 0)

    def test_csv_format_and_invalid_columns(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        upload = SimpleUploadedFile(
            "courses.csv",
            f"room,title,date,start,end\nA101,课程,{future},08:00,09:40\n".encode(),
        )
        form = CourseImportForm({}, {"file": upload})
        self.assertTrue(form.is_valid(), form.errors)
        bad = SimpleUploadedFile(
            "bad.csv", b"room,title,date,start,end\nA101,Oops,2026-01-01,08:00\n"
        )
        self.assertFalse(CourseImportForm({}, {"file": bad}).is_valid())

    def test_retention_cascades_personal_information_audit_and_sessions(self):
        b, code = self.guest()
        Booking.objects.filter(pk=b.pk).update(
            created_at=timezone.now()
            - timedelta(days=settings.RETENTION_DAYS, seconds=1)
        )
        self.assertFalse(Booking.objects.retained().exists())
        self.client.post("/lookup/", {"code": code})
        self.assertNotIn("guest_booking", self.client.session)
        Session.objects.create(
            session_key="expired",
            session_data="",
            expire_date=timezone.now() - timedelta(hours=1),
        )
        call_command("purge_expired", stdout=io.StringIO())
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertFalse(Session.objects.filter(pk="expired").exists())

    def test_statistics_export_does_not_include_student_data(self):
        b, _ = self.guest()
        self.client.force_login(self.admin)
        response = self.client.get("/reports/?export=csv")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("A101", text)
        for private in (b.student_name, b.student_id, b.student_contact):
            self.assertNotIn(private, text)

    def test_csv_formula_injection_is_escaped(self):
        from .views import safe_csv

        self.assertEqual(safe_csv("  =1+1"), "'  =1+1")
        self.assertEqual(safe_csv("A101"), "A101")

    def test_report_date_range_uses_china_day_boundaries(self):
        b, _ = self.book()
        self.client.force_login(self.admin)
        day = timezone.localdate(b.start_at)
        response = self.client.get(f"/reports/?from={day}&to={day}")
        self.assertEqual(response.context["total"], 1)
        response = self.client.get(f"/reports/?from={day + timedelta(days=1)}")
        self.assertEqual(response.context["total"], 0)

    def test_admin_login_uses_same_rate_limiter(self):
        for _ in range(10):
            self.client.post(
                "/admin/login/", {"username": "missing-admin", "password": "wrong"}
            )
        self.assertEqual(
            self.client.post(
                "/admin/login/", {"username": "missing-admin", "password": "wrong"}
            ).status_code,
            429,
        )

    def test_pages_templates_and_health(self):
        for path in (
            "/",
            "/login/",
            "/lookup/",
            f"/rooms/{self.room.pk}/",
            "/healthz/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.client.force_login(self.admin)
        for path in (
            "/bookings/",
            "/review/",
            "/courses/import/",
            "/reports/",
            "/password/",
            "/admin/",
            "/admin/booking/user/",
            "/admin/booking/room/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_rate_limit_and_privacy_headers(self):
        for _ in range(10):
            self.client.post("/login/", {"username": "nobody", "password": "invalid"})
        self.assertEqual(
            self.client.post(
                "/login/", {"username": "nobody", "password": "invalid"}
            ).status_code,
            429,
        )
        response = self.client.get("/lookup/")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])


@skipUnless(
    connection.vendor in ("mysql", "postgresql"),
    "SQLite 不提供所需的行级锁，生产数据库上执行并发测试",
)
class ConcurrencyTests(TransactionTestCase):
    def test_simultaneous_submissions_only_one_succeeds(self):
        teacher = User.objects.create_user(username="concurrent", role="teacher")
        room = Room.objects.create(name="R1", building="教学楼", capacity=20)
        start, end = interval()
        gate = threading.Barrier(2)

        def submit(_):
            close_old_connections()
            try:
                user = User.objects.get(pk=teacher.pk)
                gate.wait(timeout=10)
                create_booking(
                    user,
                    room.pk,
                    title="并发测试",
                    start_at=start,
                    end_at=end,
                    participants=2,
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
