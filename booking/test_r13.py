"""R13: 90-day return/end retention boundaries and credential hashing."""
from datetime import timedelta
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from django.core.management import call_command
from django.contrib.auth.hashers import check_password
from .models import Booking, FurnitureLoan, Room, booking_retention_anchor

class ReturnRetentionR13Tests(TestCase):
    def setUp(self):
        self.room=Room.objects.create(name='R13-T1',building='独立测试楼')

    def test_classroom_completed_uses_confirmation_not_creation_or_end(self):
        now=timezone.now()
        expired=now-timedelta(days=100)
        b=Booking.objects.create(room=self.room,title='实际晚归还',
            start_at=expired-timedelta(hours=2),end_at=expired,
            created_at=expired-timedelta(days=10),status='completed',
            reviewed_at=now-timedelta(days=89))
        self.assertTrue(Booking.objects.retained().filter(pk=b.pk).exists())
        call_command('purge_expired',verbosity=0)
        self.assertTrue(Booking.objects.filter(pk=b.pk).exists())
        Booking.objects.filter(pk=b.pk).update(reviewed_at=now-timedelta(days=91))
        call_command('purge_expired',verbosity=0)
        self.assertFalse(Booking.objects.filter(pk=b.pk).exists())

    def test_furniture_unreturned_is_not_auto_deleted_and_returned_is_deleted(self):
        now=timezone.now()
        old=now-timedelta(days=200)
        f=FurnitureLoan.objects.create(borrower_name='借用甲',student_id='ID',phone='1234567890',items=[],created_at=old)
        call_command('purge_expired',verbosity=0)
        self.assertTrue(FurnitureLoan.objects.filter(pk=f.pk).exists())
        FurnitureLoan.objects.filter(pk=f.pk).update(returned_at=now-timedelta(days=91))
        call_command('purge_expired',verbosity=0)
        self.assertFalse(FurnitureLoan.objects.filter(pk=f.pk).exists())

    def test_security_password_not_plaintext_and_max_retention_configuration(self):
        from .services import credential_tag
        from django.contrib.auth.hashers import make_password
        raw='short'
        password_hash=make_password(raw)
        self.assertNotEqual(password_hash,raw)
        self.assertTrue(check_password(raw,password_hash))
        self.assertNotIn(raw,credential_tag('ID',raw))
        self.assertEqual(settings.BOOKING_RETENTION_DAYS,90)
        self.assertEqual(settings.FURNITURE_RETENTION_DAYS,90)
