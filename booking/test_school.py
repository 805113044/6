import io
from datetime import datetime, time, timedelta
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, TransactionTestCase
from django.utils import timezone
from openpyxl import Workbook
from unittest.mock import patch
from .models import AcademicTerm, BorrowPeriod, Booking, Room, TeachingRule, User
from .forms import CourseImportForm, BookingForm
from .school import save_school, availability
from .services import create_booking, edit_booking, import_course_rows
from .tests import seed_school, student, interval


class SchoolPolicyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.term = seed_school()
        cls.admin = User.objects.create_user(username="school-admin", is_staff=True)
        cls.room = Room.objects.create(
            name="KNOWN-101", campus="主校区", building="教学楼", floor=1, capacity=60
        )

    def book(self, period=1, day=None, room=None):
        day = day or timezone.localdate() + timedelta(days=2)
        p = BorrowPeriod.objects.get(number=period)
        return create_booking(
            AnonymousUser(),
            (room or self.room).pk,
            title="测试借用",
            start_at=timezone.make_aware(datetime.combine(day, p.start)),
            end_at=timezone.make_aware(datetime.combine(day, p.end)),
            participants=3,
            student=student(),
        )[0]

    def settings_data(self, **changes):
        data = {
            field: getattr(self.term, field)
            for field in ("name", "starts_on", "ends_on", "first_week", "booking_open")
        }
        for p in BorrowPeriod.objects.all():
            data.update({f"p{p.number}_start": p.start, f"p{p.number}_end": p.end})
        data.update(changes)
        return data

    def workbook(
        self,
        room="NEW-LAB-301",
        *,
        weekday=None,
        weeks="1-18",
        pattern="每周",
        period="第一节",
        title="正式课程",
        metadata=True,
    ):
        day = timezone.localdate() + timedelta(days=2)
        header = [
            "教室",
            "课程名称",
            "任课教师",
            "周次",
            "单双周",
            "星期",
            "节次",
            "排除日期",
        ]
        row = [
            room,
            title,
            "王老师",
            weeks,
            pattern,
            weekday or day.isoweekday(),
            period,
            "",
        ]
        if metadata:
            header += ["校区", "楼栋", "楼层", "教室类型", "容量"]
            row += ["主校区", "实验楼", 3, "实验室", 24]
        wb = Workbook()
        ws = wb.active
        ws.append(header)
        ws.append(row)
        output = io.BytesIO()
        wb.save(output)
        return SimpleUploadedFile("school.xlsx", output.getvalue())

    def parse(self, upload):
        form = CourseImportForm({}, {"file": upload})
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data["file"]

    def test_default_five_periods_and_no_custom_clock_input(self):
        periods = list(BorrowPeriod.objects.all())
        self.assertEqual(len(periods), 5)
        self.assertEqual(
            [p.group for p in periods], ["上午", "上午", "下午", "下午", "晚上"]
        )
        response = self.client.get(f"/rooms/{self.room.pk}/")
        self.assertNotContains(response, 'type="time"')
        self.assertIn("period", BookingForm().fields)
        self.assertNotIn("start", BookingForm().fields)
        start, end = interval(8)
        with self.assertRaises(ValidationError):
            create_booking(
                AnonymousUser(),
                self.room.pk,
                title="伪造时段",
                start_at=start,
                end_at=end - timedelta(minutes=1),
                participants=2,
                student=student(),
            )
        data = {
            "title": "篡改",
            "date": timezone.localdate() + timedelta(days=2),
            "period": periods[0].pk,
            "participants": 1,
            "start": "08:13",
        }
        self.assertFalse(BookingForm(data).is_valid())

    def test_school_must_be_configured_and_can_pause(self):
        AcademicTerm.objects.all().delete()
        with self.assertRaises(ValidationError):
            self.book()
        save_school(self.admin, self.settings_data())
        save_school(self.admin, self.settings_data(booking_open=False))
        with self.assertRaises(ValidationError):
            self.book()
        self.assertFalse(
            availability(
                [self.room],
                timezone.localdate() + timedelta(days=2),
                list(BorrowPeriod.objects.all()),
            )[self.room.pk][0]["free"]
        )
        save_school(self.admin, self.settings_data(booking_open=True))
        self.assertEqual(self.book().status, "approved")
        self.assertEqual(AcademicTerm.objects.count(), 1)

    def test_first_run_sync_can_wait_for_admin_semester_setup(self):
        from .timetable import sync_timetables

        AcademicTerm.objects.all().delete()
        sync_timetables()

    def test_semester_date_bounds_and_week_numbers(self):
        self.assertEqual(self.term.week_number(self.term.starts_on), 1)
        save_school(self.admin, self.settings_data(first_week=3))
        term = AcademicTerm.objects.get()
        self.assertEqual(term.week_number(term.starts_on + timedelta(days=7)), 4)
        response = self.client.get("/schedule/?week=4")
        self.assertContains(response, "第 4 周")
        save_school(
            self.admin,
            self.settings_data(ends_on=timezone.localdate() + timedelta(days=1)),
        )
        with self.assertRaises(ValidationError):
            self.book()

    def test_period_clock_changes_are_admin_only_and_cannot_invalidate_future_bookings(
        self,
    ):
        b = self.book()
        with self.assertRaises(ValidationError):
            save_school(self.admin, self.settings_data(p1_start=time(8, 10)))
        self.assertEqual(BorrowPeriod.objects.get(number=1).start, time(8))
        with self.assertRaises(ValidationError):
            save_school(self.admin, self.settings_data(p2_start=time(9)))
        self.assertEqual(Client().get("/manage/school/").status_code, 302)
        save_school(self.admin, self.settings_data(p3_start=time(14, 10)))
        self.assertEqual(BorrowPeriod.objects.get(number=3).start, time(14, 10))
        self.assertEqual(
            b.start_at,
            timezone.make_aware(
                datetime.combine(timezone.localdate() + timedelta(days=2), time(8))
            ),
        )

    def test_empty_install_excel_creates_rooms_and_blocks_course_period(self):
        Room.objects.all().delete()
        rows = self.parse(self.workbook())
        self.assertEqual(import_course_rows(self.admin, rows, dry_run=False), (1, 0))
        room = Room.objects.get(name="NEW-LAB-301")
        self.assertEqual((room.floor, room.capacity, room.room_type), (3, 24, "lab"))
        with self.assertRaises(ValidationError):
            self.book(room=room)
        self.assertEqual(self.book(period=2, room=room).status, "approved")
        day = timezone.localdate() + timedelta(days=2)
        response = self.client.get(
            f"/?date={day}&period={BorrowPeriod.objects.get(number=1).pk}"
        )
        self.assertEqual(response.context["page"].paginator.count, 0)

    def test_preview_rolls_back_auto_rooms_and_courses(self):
        rows = self.parse(self.workbook())
        import_course_rows(self.admin, rows, dry_run=True)
        self.assertFalse(Room.objects.filter(name="NEW-LAB-301").exists())
        self.assertFalse(TeachingRule.objects.exists())
        self.assertFalse(Booking.objects.exists())

    def test_conflict_rolls_back_everything_including_auto_rooms(self):
        rows = self.parse(self.workbook())
        second = {**rows[0], "rule": {**rows[0]["rule"], "title": "冲突课程"}}
        with self.assertRaises(ValidationError):
            import_course_rows(self.admin, [rows[0], second], dry_run=False)
        self.assertFalse(Room.objects.filter(name="NEW-LAB-301").exists())
        self.assertFalse(TeachingRule.objects.exists())

    def test_one_click_excel_http_import(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            "/courses/import/", {"file": self.workbook(), "confirm": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["result"]["saved"])
        self.assertEqual(response.context["result"]["new_rooms"], 1)
        self.assertTrue(Room.objects.filter(name="NEW-LAB-301").exists())

    def test_unknown_room_metadata_stays_unknown_and_existing_data_not_overwritten(
        self,
    ):
        rows = self.parse(self.workbook(metadata=False))
        import_course_rows(self.admin, rows, dry_run=False)
        room = Room.objects.get(name="NEW-LAB-301")
        self.assertIsNone(room.capacity)
        self.assertIsNone(room.floor)
        self.assertEqual(self.book(period=2, room=room).status, "approved")
        rows = self.parse(self.workbook(room=self.room.name))
        import_course_rows(self.admin, rows, dry_run=False)
        self.room.refresh_from_db()
        self.assertEqual((self.room.capacity, self.room.room_type), (60, "lecture"))

    def test_manual_create_and_edit_lab(self):
        self.client.force_login(self.admin)
        data = {
            "name": "手工实验室",
            "campus": "主校区",
            "building": "实验楼",
            "floor": 4,
            "capacity": 30,
            "room_type": "lab",
            "is_active": "on",
        }
        self.assertEqual(self.client.post("/manage/rooms/new/", data).status_code, 302)
        room = Room.objects.get(name="手工实验室")
        self.assertEqual(room.room_type, "lab")
        data["capacity"] = 40
        self.assertEqual(
            self.client.post(f"/manage/rooms/{room.pk}/", data).status_code, 302
        )
        room.refresh_from_db()
        self.assertEqual(room.capacity, 40)
        self.assertEqual(Client().post("/manage/rooms/new/", data).status_code, 302)

    def test_partial_class_occupies_whole_borrow_period(self):
        day = timezone.localdate() + timedelta(days=2)
        start = timezone.make_aware(datetime.combine(day, time(8, 30)))
        end = start + timedelta(minutes=20)
        rows = [
            {
                "room": self.room.name,
                "title": "短课",
                "teacher": "李老师",
                "start_at": start,
                "end_at": end,
            }
        ]
        import_course_rows(self.admin, rows, dry_run=False)
        matrix = availability([self.room], day, list(BorrowPeriod.objects.all()))[
            self.room.pk
        ]
        self.assertFalse(matrix[0]["free"])
        self.assertTrue(matrix[1]["free"])
        with self.assertRaises(ValidationError):
            self.book()

    def test_course_odd_even_is_not_a_borrowing_rule(self):
        day = timezone.localdate() + timedelta(days=2)
        number = self.term.week_number(day)
        matching = "单周" if number % 2 else "双周"
        opposite = "双周" if number % 2 else "单周"
        rows = self.parse(
            self.workbook(room=self.room.name, pattern=opposite, metadata=False)
        )
        import_course_rows(self.admin, rows, dry_run=False)
        b = self.book()
        self.assertEqual(b.status, "approved")
        # A matching official class collides with this already-booked period; import must roll back.
        rows = self.parse(
            self.workbook(room=self.room.name, pattern=matching, metadata=False)
        )
        with self.assertRaises(ValidationError):
            import_course_rows(self.admin, rows, dry_run=False)

    def test_combined_chinese_week_spec_and_section_range(self):
        rows = self.parse(self.workbook(weeks="1-16(单)", pattern="", period="1-2"))
        rule = rows[0]["rule"]
        self.assertEqual(rule["week_pattern"], "odd")
        self.assertEqual((rule["start"], rule["end"]), (time(8), time(11, 40)))

    def test_one_month_cap_and_edit_cannot_extend_lifetime(self):
        self.assertLessEqual(settings.RETENTION_DAYS, 30)
        b = self.book()
        old = timezone.now() - timedelta(days=settings.RETENTION_DAYS - 1)
        Booking.objects.filter(pk=b.pk).update(created_at=old)
        start, end = interval(14, offset=3)
        with self.assertRaises(ValidationError):
            edit_booking(
                AnonymousUser(),
                b.pk,
                room_id=b.room_id,
                title="延后",
                start_at=start,
                end_at=end,
                participants=1,
                student=student(),
                guest_authorized=True,
                expected_version=1,
            )
        self.assertLess(settings.BOOKING_ADVANCE_DAYS, settings.RETENTION_DAYS)

    def test_far_future_date_course_is_kept_as_configuration(self):
        from .timetable import sync_timetables

        day = timezone.localdate() + timedelta(days=50)
        start = timezone.make_aware(datetime.combine(day, time(8)))
        end = timezone.make_aware(datetime.combine(day, time(9, 40)))
        import_course_rows(
            self.admin,
            [
                {
                    "room": self.room.name,
                    "title": "远期正式课程",
                    "teacher": "王老师",
                    "start_at": start,
                    "end_at": end,
                }
            ],
            dry_run=False,
        )
        self.assertEqual(TeachingRule.objects.count(), 1)
        self.assertFalse(Booking.objects.exists())
        with patch(
            "django.utils.timezone.now",
            return_value=timezone.now() + timedelta(days=45),
        ):
            sync_timetables()
            self.assertTrue(Booking.objects.filter(kind="course").exists())
            with self.assertRaises(ValidationError):
                self.book(day=day)

    def test_ongoing_course_also_blocks_later_overlapping_period(self):
        day = timezone.localdate() + timedelta(days=2)
        clock = timezone.make_aware(datetime.combine(day, time(13)))
        start = timezone.make_aware(datetime.combine(day, time(12, 30)))
        end = timezone.make_aware(datetime.combine(day, time(15)))
        with patch("django.utils.timezone.now", return_value=clock):
            import_course_rows(
                self.admin,
                [
                    {
                        "room": self.room.name,
                        "title": "进行中的课程",
                        "teacher": "王老师",
                        "start_at": start,
                        "end_at": end,
                    }
                ],
                dry_run=False,
            )
            with self.assertRaises(ValidationError):
                self.book(period=3, day=day)


class LegacyCourseMigrationTests(TransactionTestCase):
    def test_existing_date_class_becomes_durable_teaching_configuration(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate([("booking", "0006_seed_five_periods")])
        old = executor.loader.project_state(
            [("booking", "0006_seed_five_periods")]
        ).apps
        room = old.get_model("booking", "Room").objects.create(
            name="旧课表教室", building="教学楼"
        )
        start = timezone.make_aware(
            datetime.combine(timezone.localdate() + timedelta(days=5), time(8))
        )
        b = old.get_model("booking", "Booking").objects.create(
            room_id=room.pk,
            title="原有正式课表",
            kind="course",
            status="approved",
            start_at=start,
            end_at=start + timedelta(hours=1),
        )
        original_created = b.created_at
        executor = MigrationExecutor(connection)
        executor.migrate([("booking", "0007_keep_legacy_scheduled_courses")])
        b = Booking.objects.get(pk=b.pk)
        self.assertTrue(b.course_key)
        self.assertEqual(b.created_at, original_created)
        self.assertEqual(
            TeachingRule.objects.filter(room_id=room.pk, title="原有正式课表").count(),
            1,
        )
