"""Regression tests for per-period official-course override & booking retention."""
from datetime import timedelta, time, datetime
from django.conf import settings
from django.test import TestCase, Client
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from django.core.management import call_command
from booking.models import User, Room, AcademicTerm, BorrowPeriod, Booking, FurnitureLoan
from booking.services import create_booking
from booking.timetable import import_timetable, sync_timetables, release_imported_course_period
from booking.school import availability


class ClassroomReleaseR12Tests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.day = today + timedelta(days=2)
        monday = today - timedelta(days=today.weekday())
        self.term = AcademicTerm.objects.create(
            name='学期', starts_on=monday, ends_on=monday+timedelta(days=130),
            first_week=1, booking_open=True,
        )
        for number, (a, b) in enumerate([(time(8),time(9,40)), (time(10),time(11,40)),
            (time(14),time(15,40)), (time(16),time(17,40)), (time(19),time(21))], 1):
            BorrowPeriod.objects.update_or_create(number=number, defaults={'start':a,'end':b})
        self.first = BorrowPeriod.objects.get(number=1)
        self.second = BorrowPeriod.objects.get(number=2)
        self.room = Room.objects.create(name='R12A101', building='教学楼', is_active=True)
        self.admin = User.objects.create_superuser(username='r12-admin', password='Admin-test-password!2026')
        self.teacher = User.objects.create_user(username='r12-teacher', password='Teacher-test-password!2026', role='teacher', display_name='李教师')
        self.other = User.objects.create_user(username='r12-other', password='Teacher-test-password!2026', role='teacher', display_name='张教师')
        day = self.day
        monday=day-timedelta(days=day.weekday())
        self.rule={
            'room':self.room.name,
            'rule':{'title':'大学物理','teacher':'李教师','term_start':self.term.week_one_monday,
             'week_start':(day-self.term.week_one_monday).days//7+1,
             'week_end':(day-self.term.week_one_monday).days//7+1,
             'week_pattern':'all','weekday':day.isoweekday(),
             'start':self.first.start,'end':self.second.end,'excluded_dates':[]},
        }
        import_timetable(self.admin,[self.rule],dry_run=False)

    def test_release_only_selected_period_survives_sync(self):
        self.assertFalse(availability([self.room],self.day,[self.first])[self.room.pk][0]['free'])
        release_imported_course_period(self.teacher,self.room.pk,self.day,self.first.pk,'李教师')
        cells=availability([self.room],self.day,[self.first,self.second])[self.room.pk]
        self.assertTrue(cells[0]['free'])
        self.assertFalse(cells[1]['free'])
        self.assertFalse(cells[0]['entries'])
        sync_timetables()
        cells=availability([self.room],self.day,[self.first,self.second])[self.room.pk]
        self.assertTrue(cells[0]['free'])
        self.assertFalse(cells[1]['free'])
        with self.assertRaises(ValidationError):
            release_imported_course_period(self.teacher,self.room.pk,self.day,self.first.pk,'李教师')

    def test_teacher_must_match_name_and_guest_cannot_release(self):
        with self.assertRaises(PermissionDenied):
            release_imported_course_period(self.other,self.room.pk,self.day,self.first.pk,'李教师')
        with self.assertRaises(PermissionDenied):
            release_imported_course_period(AnonymousUser(),self.room.pk,self.day,self.first.pk,'李教师')
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get('/schedule/?view=day&date='+self.day.isoformat()),'临时开放此节')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/schedule/?view=day&date='+self.day.isoformat()),'临时开放此节')

    def test_admin_one_click_and_release_makes_booking_possible(self):
        self.client.force_login(self.admin)
        response=self.client.post('/courses/release-slot/',{
            'room_id':self.room.pk,'period_id':self.first.pk,'date':self.day.isoformat(),
            'teacher_name':'李教师','view':'day',
        })
        self.assertEqual(response.status_code,302)
        cells=availability([self.room],self.day,[self.first,self.second])[self.room.pk]
        self.assertTrue(cells[0]['free'])
        self.assertFalse(cells[1]['free'])
        start=timezone.make_aware(datetime.combine(self.day,self.first.start))
        end=timezone.make_aware(datetime.combine(self.day,self.first.end))
        b,_=create_booking(AnonymousUser(),self.room.pk,title='学习',start_at=start,end_at=end,participants=1,
            student={'student_name':'学生','student_id':'r12-id','student_department':'学院','student_contact':'12345678','edit_password':'valid-password!2026'})
        self.assertEqual(b.status,'approved')

    def test_room_and_furniture_90_days_after_return_or_end(self):
        self.assertEqual(settings.BOOKING_RETENTION_DAYS,90)
        self.assertEqual(settings.FURNITURE_RETENTION_DAYS,90)
        now=timezone.now()
        past=now-timedelta(days=91)
        booking=Booking.objects.create(room=self.room, title='已结束借用',
            start_at=past-timedelta(hours=1),end_at=past,kind='booking',status='approved')
        booking_future=Booking.objects.create(room=self.room,title='将来使用',
            start_at=now+timedelta(hours=1),end_at=now+timedelta(hours=2),
            created_at=past-timedelta(days=100),kind='booking',status='approved')
        furniture=FurnitureLoan.objects.create(borrower_name='归还人',student_id='OLD',
            phone='1234567890',items=[],created_at=past-timedelta(days=100),returned_at=past)
        furniture_open=FurnitureLoan.objects.create(borrower_name='未归还人',student_id='OPEN',
            phone='1234567890',items=[],created_at=past-timedelta(days=100))
        from booking.management.commands.purge_expired import purge
        purge()
        self.assertFalse(Booking.objects.filter(pk=booking.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=booking_future.pk).exists())
        self.assertFalse(FurnitureLoan.objects.filter(pk=furniture.pk).exists())
        self.assertTrue(FurnitureLoan.objects.filter(pk=furniture_open.pk).exists())
