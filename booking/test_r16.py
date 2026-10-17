"""R16 regressions: new-password policy and unchanged legacy checks."""
from datetime import datetime, timedelta
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import authenticate
from booking.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.http import QueryDict
from django.utils import timezone
from booking.forms import StudentBookingForm, BookingEditForm, FurnitureLoanForm, AdministratorEditForm, AdministratorCreateForm
from booking.models import AcademicTerm, Booking, BorrowPeriod, Room
from booking.password_policy_r16 import validate_new_password
from booking.services import create_booking, verify_guest, lookup_guest_bookings

class NewPasswordPolicyR16Tests(TestCase):
    def setUp(self):
        day=timezone.localdate()
        start=day-timedelta(days=day.weekday())
        AcademicTerm.objects.update_or_create(pk=1,defaults={
            'name':'R16测试学期','starts_on':start,
            'ends_on':start+timedelta(weeks=20),'booking_open':True})
        self.period,_=BorrowPeriod.objects.update_or_create(number=1,defaults={
            'start':datetime.strptime('08:00','%H:%M').time(),
            'end':datetime.strptime('09:40','%H:%M').time()})
        self.room=Room.objects.create(name='R16A101',building='测试楼')

    def info(self,password='A12345'):
        return {'student_name':'测试学生','student_id':'R16-001','student_department':'计算机',
            'student_contact':'student@example.test','edit_password':password}

    def test_new_password_rules_across_django_and_guest_services(self):
        for valid in ('Qz7p4M','B8k3y2','7Jx28P','AA12bb','测试a12345'):
            with self.subTest(valid=valid):
                validate_new_password(valid)
                validate_password(valid)
        for invalid in ('', 'a1234', 'abcdef', '123456', 'abcd!@', '１２３４５a'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):validate_new_password(invalid)
                with self.assertRaises(ValidationError):validate_password(invalid)
        future=timezone.localdate()+timedelta(days=2)
        a=timezone.make_aware(datetime.combine(future,self.period.start))
        b=timezone.make_aware(datetime.combine(future,self.period.end))
        for pwd in ('1','abcdef','123456','Ab123'):
            with self.subTest(pwd=pwd):
                with self.assertRaises(ValidationError):
                    create_booking(AnonymousUser(),self.room.pk,title='测试借用',
                        start_at=a,end_at=b,participants=1,student=self.info(pwd))
        self.assertFalse(Booking.objects.exists())

    def test_new_password_forms_and_change_only(self):
        future=timezone.localdate()+timedelta(days=2)
        fields={'title':'活动','date':future.isoformat(),'period':str(self.period.pk),
            'participants':1,**self.info('Qz7p4M'),'edit_password_confirm':'Qz7p4M'}
        form=StudentBookingForm(fields)
        self.assertTrue(form.is_valid(),form.errors)
        for pwd in ('a1234','abcdef','123456'):
            form=StudentBookingForm({**fields,'edit_password':pwd,'edit_password_confirm':pwd})
            self.assertFalse(form.is_valid())
            self.assertIn('edit_password',form.errors)
        # Multi-item furniture submissions are Django QueryDicts, not plain dicts:
        # FurnitureLoanForm.clean uses .getlist() for repeated input names.
        loan=QueryDict('',mutable=True)
        loan.update({'borrower_name':'测试学生','student_id':'R16-001',
            'phone':'13800138000','loan_password':'Qz7p4M',
            'loan_password_confirm':'Qz7p4M'})
        loan.setlist('item_name',['__OTHER__'])
        loan.setlist('item_quantity',['1'])
        loan.setlist('item_custom_name',['笔记架'])
        good=FurnitureLoanForm(loan)
        self.assertNotIn('loan_password',good.errors)
        for bad in ('123456','abcdef','a1'):
            invalid_loan=loan.copy()
            invalid_loan['loan_password']=bad
            invalid_loan['loan_password_confirm']=bad
            form=FurnitureLoanForm(invalid_loan)
            self.assertIn('loan_password',form.errors)
        change=BookingEditForm({**fields,'room':str(self.room.pk),'edit_password':'Qz7p4M'},
            rooms=Room.objects.all(),admin=False)
        self.assertNotIn('edit_password',change.errors)
        # Leaving the change-password field blank preserves an existing password.
        unchanged=BookingEditForm({**fields,'room':str(self.room.pk),'edit_password':''},
            rooms=Room.objects.all(),admin=False)
        self.assertNotIn('edit_password',unchanged.errors)

    def test_old_one_character_password_still_authenticates(self):
        future=timezone.localdate()+timedelta(days=2)
        a=timezone.make_aware(datetime.combine(future,self.period.start))
        b=timezone.make_aware(datetime.combine(future,self.period.end))
        legacy=Booking.objects.create(room=self.room,title='旧预约',start_at=a,end_at=b,
            status='approved',student_name='学生',student_id='R16-001',
            edit_password_hash=make_password('1'))
        self.assertTrue(verify_guest(legacy,'R16-001','1'))
        self.assertEqual([x.pk for x in lookup_guest_bookings('R16-001','1')],[legacy.pk])
        self.assertTrue(check_password('a',make_password('a')))
        admin=User.objects.create_user(username='r16-staff',password='1',is_staff=True)
        self.assertEqual(authenticate(username='r16-staff',password='1').pk,admin.pk)

    def test_admin_new_or_changed_password_policy(self):
        manager=User.objects.create_user(username='r16-manager',password='legacy',is_staff=True)
        base={'display_name':'测试管理员','department':'测试院系','is_active':'on'}
        form=AdministratorEditForm({**base,'password1':'Qz7p4M',
            'password2':'Qz7p4M'},instance=manager)
        self.assertTrue(form.is_valid(),form.errors)
        invalid=AdministratorEditForm({**base,'password1':'abcdef',
            'password2':'abcdef'},instance=manager)
        self.assertFalse(invalid.is_valid())
        # Admin edit form validates in form.clean(), so Django places these
        # errors under __all__ rather than the individual password1 field.
        self.assertIn('__all__',invalid.errors)
        self.assertTrue(any('英文字母' in message for message in invalid.errors['__all__']))
        valid_create=AdministratorCreateForm({'username':'r16-new',
            'display_name':'新管理员','department':'教学部',
            'password1':'Qz7p4M','password2':'Qz7p4M'})
        self.assertTrue(valid_create.is_valid(),valid_create.errors)
        invalid_create=AdministratorCreateForm({'username':'r16-bad',
            'display_name':'新管理员','department':'教学部',
            'password1':'abcdef','password2':'abcdef'})
        self.assertFalse(invalid_create.is_valid())
