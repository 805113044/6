"""R14 regression coverage: permission boundary, matching, status and pagination."""
from datetime import datetime, timedelta
from django.contrib.auth.models import AnonymousUser
from django.test import Client, TestCase
from django.utils import timezone
from .models import Booking, Room, User

class ReviewSearchR14Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin=User.objects.create_superuser(username='r14-admin',password='r14-superuser-strong!2026')
        cls.room=Room.objects.create(name='R14-A101',building='A楼')
        cls.other_room=Room.objects.create(name='R14-B201',building='B楼')
        cls.day=timezone.localdate()+timedelta(days=2)
        start=timezone.make_aware(datetime.combine(cls.day,datetime.min.time()))+timedelta(hours=10)
        cls.one=Booking.objects.create(room=cls.room,student_name='王甲',student_id='R14-SID-1',
            title='R14物理研讨',kind='booking',status='approved',start_at=start,end_at=start+timedelta(hours=1))
        cls.two=Booking.objects.create(room=cls.other_room,student_name='王乙',student_id='R14-SID-2',
            title='R14数学研讨',kind='booking',status='returned',start_at=start+timedelta(days=1),end_at=start+timedelta(days=1,hours=1))
    def test_only_staff_can_search(self):
        self.assertNotEqual(Client().get('/review/?q=R14-SID-1').status_code,200)
        staff=Client();staff.force_login(self.admin)
        self.assertEqual(staff.get('/review/').status_code,200)
    def test_exact_id_date_and_room_search(self):
        self.client.force_login(self.admin)
        response=self.client.get('/review/',{'status':'all','q':'R14-SID-1','date':self.day.isoformat()})
        self.assertEqual([x.pk for x in response.context['page']],[self.one.pk])
        room=self.client.get('/review/',{'status':'all','q':'R14-B201'})
        self.assertEqual([x.pk for x in room.context['page']],[self.two.pk])
    def test_status_kind_and_search_param_persist(self):
        self.client.force_login(self.admin)
        response=self.client.get('/review/',{'status':'approved','q':'R14','kind':'booking'})
        self.assertEqual([x.pk for x in response.context['page']],[self.one.pk])
        self.assertIn('q=R14',response.context['query_string'])
        self.assertIn('kind=booking',response.context['query_string'])
        self.assertContains(response,'查询预约记录')
    def test_new_peppered_passwords_and_old_passwords_both_verify(self):
        from django.contrib.auth.hashers import make_password, check_password, PBKDF2PasswordHasher
        newest = make_password("one-char-test")
        self.assertTrue(newest.startswith("pbkdf2_sha256_peppered_v1$"))
        self.assertTrue(check_password("one-char-test", newest))
        self.assertFalse(check_password("wrong", newest))
        legacy = PBKDF2PasswordHasher().encode("old-user-password", "r14unique-salt")
        self.assertTrue(check_password("old-user-password", legacy))
        self.assertFalse(check_password("wrong", legacy))

    def test_invalid_date_is_safe(self):
        self.client.force_login(self.admin)
        response=self.client.get('/review/',{'status':'all','date':'invalid','q':'R14-SID-1'})
        self.assertEqual([x.pk for x in response.context['page']],[self.one.pk])
