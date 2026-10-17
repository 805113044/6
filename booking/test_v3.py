"""V3 safety/regression checks; use MySQL CI for final release certification."""
from datetime import timedelta
from django.test import TestCase, override_settings
from django.http import QueryDict
from django.utils import timezone
from .models import AcademicTerm, Booking, BorrowPeriod, FurnitureLoan, FurnitureStock, Room, User
from .forms import FurnitureLoanForm
from .v3 import _make_loan


@override_settings(RETENTION_DAYS=21)
class FurnitureInventoryV3Tests(TestCase):
    def setUp(self):
        self.stock = FurnitureStock.objects.create(name='椅子', total_count=10)
        self.staff = User.objects.create_superuser(
            username='v3-staff', password='strong-v3-test-password-09876', display_name='管理人'
        )

    def data(self, **changes):
        payload = {
            'borrower_name': '测试学生', 'student_id': '20260001',
            'phone': '13800138000', 'purpose': '班级活动',
            'loan_password': 'student-v3-secret', 'loan_password_confirm': 'student-v3-secret',
            'item_name': ['椅子'], 'item_quantity': ['3'],
        }
        payload.update(changes)
        return payload

    def test_r11_only_admin_items_in_dropdown_and_other_option(self):
        from .models import FurnitureStock
        FurnitureStock.objects.create(name='旧物品', total_count=1, is_active=False)
        page = self.client.get('/furniture/')
        self.assertContains(page, 'name="item_name"')
        self.assertContains(page, 'value="__OTHER__"')
        self.assertContains(page, '椅子（剩余 10）')
        self.assertNotContains(page, '<option value="旧物品"')

    def test_r11_post_redirect_get_no_duplicate_inventory_reduction(self):
        response = self.client.post('/furniture/', self.data())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('/furniture/'))
        self.assertEqual(FurnitureLoan.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available, 7)
        self.client.get(response['Location'])
        self.client.get('/furniture/')   # browser refresh GET, never repeats POST
        self.assertEqual(FurnitureLoan.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available, 7)

    def test_r11_other_untracked_is_recorded_but_not_charged_to_inventory(self):
        response = self.client.post('/furniture/', self.data(
            item_name=['__OTHER__'], item_custom_name=['支架'], item_quantity=['2']))
        self.assertEqual(response.status_code, 302)
        loan = FurnitureLoan.objects.get()
        self.assertEqual(loan.items, [{'name':'支架', 'quantity':2,'untracked':True}])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.loaned_count, 0)
        # Even after staff creates an item with this name, old untracked returns must not affect it.
        newly_tracked = FurnitureStock.objects.create(name='支架', total_count=50, loaned_count=4)
        self.client.post('/furniture/return/', {'student_id':'20260001','password':'student-v3-secret'})
        newly_tracked.refresh_from_db()
        self.assertEqual(newly_tracked.loaned_count, 4)

    def test_r11_reject_forged_unknown_inventory_and_invalid_other(self):
        from .models import FurnitureStock
        for item_name, custom in [('假冒库存', ''), ('__OTHER__',''), ('__OTHER__', '椅子')]:
            with self.subTest(name=item_name,custom=custom):
                result=self.client.post('/furniture/', self.data(
                    item_name=[item_name], item_custom_name=[custom]))
                self.assertEqual(result.status_code, 200)
        self.assertEqual(FurnitureLoan.objects.count(), 0)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.loaned_count, 0)

    def test_r11_purge_expired_bookings_and_furniture_keep_inventory_reserved(self):
        from django.core.management import call_command
        from django.conf import settings
        from django.utils import timezone
        from datetime import timedelta
        from .models import Booking, Room
        import io
        now=timezone.now()
        post=self.client.post('/furniture/', self.data())
        self.assertEqual(post.status_code,302)
        loan=FurnitureLoan.objects.get()
        FurnitureLoan.objects.filter(pk=loan.pk).update(created_at=now-timedelta(days=150))
        room=Room.objects.create(name='R11-RET', building='测试楼')
        booking=Booking.objects.create(room=room, title='较早创建但未来使用',
            start_at=now+timedelta(hours=1), end_at=now+timedelta(hours=2),
            created_at=now-timedelta(days=150))
        call_command('purge_expired',stdout=io.StringIO())
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())
        self.assertTrue(FurnitureLoan.objects.filter(pk=loan.pk).exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.loaned_count,3)
        # Returning a loan starts its 90-day clock. Creation age cannot delete it.
        FurnitureLoan.objects.filter(pk=loan.pk).update(returned_at=now-timedelta(days=91))
        expired=now-timedelta(days=91)
        Booking.objects.filter(pk=booking.pk).update(
            start_at=expired-timedelta(hours=1), end_at=expired,
        )
        call_command('purge_expired',stdout=io.StringIO())
        self.assertFalse(Booking.objects.filter(pk=booking.pk).exists())
        self.assertFalse(FurnitureLoan.objects.filter(pk=loan.pk).exists())
        self.stock.refresh_from_db()
        # Test deliberately marks returned_at directly; no phantom stock credit.
        self.assertEqual(self.stock.loaned_count,3)

    def test_public_dashboard_inventory_and_registration(self):
        resp = self.client.get('/furniture/')
        self.assertContains(resp, '公开借用登记表')
        self.assertContains(resp, '椅子')
        response = self.client.post('/furniture/', self.data())
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get('/furniture/'), '椅子')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available, 7)
        public = self.client.get('/furniture/')
        self.assertContains(public, '测试学生')
        self.assertNotContains(public, '13800138000')
        self.assertContains(public, '20260001')
        self.assertContains(public, '公开借用登记表（所有用户可见）')
        self.assertContains(public, '公开信息提示')
        self.assertNotContains(public, 'name="consent"')
        self.assertTrue(public.content.index('登记借用'.encode()) < public.content.index('公开借用登记表（所有用户可见）'.encode()) < public.content.index('学生自主归还'.encode()))
        loan = FurnitureLoan.objects.get()
        self.assertTrue(loan.return_code_hash)
        self.assertTrue(loan.public_consent)
        self.assertTrue(loan.public_student_id)

    def test_student_return_by_student_id_and_password_restores_stock(self):
        from django.contrib.auth.hashers import check_password
        self.client.post('/furniture/', self.data())
        first = FurnitureLoan.objects.get()
        self.assertTrue(check_password('student-v3-secret', first.return_code_hash))
        self.client.post('/furniture/return/', {'student_id':'20260001','password':'wrong'})
        first.refresh_from_db()
        self.assertIsNone(first.returned_at)
        # Same student/password: return all matching outstanding loans at once.
        self.client.post('/furniture/', self.data(item_quantity=['2']))
        self.assertEqual(FurnitureLoan.objects.count(),2)
        self.client.post('/furniture/return/', {'student_id':'20260001','password':'student-v3-secret'})
        self.assertFalse(FurnitureLoan.objects.filter(returned_at__isnull=True).exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available, 10)

    def test_wrong_password_and_other_student_id_never_return(self):
        self.client.post('/furniture/', self.data())
        self.client.post('/furniture/return/', {'student_id':'other','password':'student-v3-secret'})
        self.client.post('/furniture/return/', {'student_id':'20260001','password':'bad-password'})
        self.assertIsNone(FurnitureLoan.objects.get().returned_at)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available,7)

    def test_admin_delete_record_requires_staff_and_releases_stock(self):
        self.client.post('/furniture/', self.data())
        loan = FurnitureLoan.objects.get()
        self.client.post(f'/furniture/loan/{loan.pk}/delete/', {'confirm_release':'yes'})
        self.assertTrue(FurnitureLoan.objects.filter(pk=loan.pk).exists())
        self.client.force_login(self.staff)
        self.assertContains(self.client.get('/furniture/'), '删除记录')
        self.client.post(f'/furniture/loan/{loan.pk}/delete/', {})
        self.assertTrue(FurnitureLoan.objects.filter(pk=loan.pk).exists())
        self.client.post(f'/furniture/loan/{loan.pk}/delete/', {'confirm_release':'yes'})
        self.assertFalse(FurnitureLoan.objects.filter(pk=loan.pk).exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available,10)

    def test_new_password_required_and_confirmation_consistent(self):
        self.client.post('/furniture/', self.data(loan_password='mismatch'))
        self.assertEqual(FurnitureLoan.objects.count(),0)
        self.client.post('/furniture/', self.data(loan_password='a',loan_password_confirm='a'))
        self.assertEqual(FurnitureLoan.objects.count(),0)
        self.client.post('/furniture/', self.data(loan_password='Qz7p4M',loan_password_confirm='Qz7p4M'))
        self.assertEqual(FurnitureLoan.objects.count(),1)
        self.assertContains(self.client.get('/furniture/'), '学生自主归还')
        self.assertContains(self.client.get('/furniture/'), 'name="password"')
        self.assertNotContains(self.client.get('/furniture/'), 'name="loan_id"')

    def test_capacity_and_staff_return_without_student_id(self):
        self.client.post('/furniture/', self.data(item_quantity=['9']))
        self.client.post('/furniture/', self.data(item_quantity=['2']))
        self.assertEqual(FurnitureLoan.objects.count(), 1)
        loan = FurnitureLoan.objects.get()
        self.client.force_login(self.staff)
        self.client.post(f'/furniture/{loan.pk}/return/')
        loan.refresh_from_db()
        self.assertEqual(loan.return_source, 'admin')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available, 10)

    def test_preexisting_r3_consent_does_not_retroactively_disclose_id(self):
        FurnitureLoan.objects.create(
            borrower_name='R3历史姓名', student_id='secret-student-r3-123',
            phone='9876543210', items=[{'name':'椅子','quantity':1}],
            public_consent=True,
        )
        page = self.client.get('/furniture/')
        self.assertContains(page, 'R3历史姓名')
        self.assertNotContains(page, 'secret-student-r3-123')
        self.assertNotContains(page, '9876543210')

    def test_registration_without_consent_checkbox_and_admin_display_name(self):
        response = self.client.post('/furniture/', self.data())
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get('/furniture/'), '椅子')
        self.client.force_login(self.staff)
        response = self.client.post('/furniture/', self.data(borrower_name=''))
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get('/furniture/'), '椅子')
        loan = FurnitureLoan.objects.order_by('-pk').first()
        self.assertEqual(loan.borrower_name, '管理人')
        self.assertEqual(loan.registered_by_id, self.staff.pk)
        public = self.client.get('/furniture/')
        self.assertContains(public, '管理人')

    def test_preexisting_v2_loan_remains_private(self):
        FurnitureLoan.objects.create(
            borrower_name='历史学生', student_id='v2id', phone='12345678',
            items=[{'name': '椅子', 'quantity': 1}],
        )
        response = self.client.get('/furniture/')
        self.assertNotContains(response, '历史学生')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get('/furniture/'), '历史学生')

    def test_classroom_admin_can_return_without_student_fields(self):
        room = Room.objects.create(name='X101', building='测试教学楼')
        booking = Booking.objects.create(
            room=room, title='活动', participants=2,
            student_name='同学甲', status='approved', kind='booking',
            start_at=timezone.now()+timedelta(hours=1),
            end_at=timezone.now()+timedelta(hours=3)
        )
        self.client.force_login(self.staff)
        response = self.client.post(f'/bookings/{booking.pk}/admin-return/')
        self.assertEqual(response.status_code, 302)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'completed')

    def test_admin_can_set_own_display_name(self):
        self.client.force_login(self.staff)
        response = self.client.post('/furniture/operator/name/', {'display_name': '张管理老师'})
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.display_name, '张管理老师')

    def test_stock_edit_rename_total_soft_delete_and_public_visibility(self):
        # Stock modifications must be staff-only, and soft-delete must retain history.
        anonymous = self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                                     {'name': '新椅子', 'total_count': 16})
        self.assertEqual(anonymous.status_code, 302)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.name, '椅子')
        self.client.force_login(self.staff)
        response = self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                                    {'name': '新椅子', 'total_count': 16})
        self.assertEqual(response.status_code, 302)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.name, '新椅子')
        self.assertEqual(self.stock.total_count, 16)
        self.assertContains(self.client.get('/furniture/'), '保存修改')
        response = self.client.post(f'/furniture/stock/{self.stock.pk}/delete/')
        self.assertEqual(response.status_code, 302)
        self.stock.refresh_from_db()
        self.assertFalse(self.stock.is_active)
        self.client.logout()
        public = self.client.get('/furniture/')
        self.assertNotContains(public, '新椅子')
        self.assertNotContains(public, '保存修改')

    def test_stock_active_loan_blocks_rename_and_delete_but_allows_total_update(self):
        self.client.post('/furniture/', self.data(item_quantity=['3']))
        self.client.force_login(self.staff)
        self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                         {'name': '改名椅子', 'total_count': 15})
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.name, '椅子')
        self.assertEqual(self.stock.total_count, 10)
        self.client.post(f'/furniture/stock/{self.stock.pk}/delete/')
        self.stock.refresh_from_db()
        self.assertTrue(self.stock.is_active)
        self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                         {'name': '椅子', 'total_count': 2})
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.total_count, 10)
        self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                         {'name': '椅子', 'total_count': 15})
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.total_count,self.stock.loaned_count,self.stock.available), (15,3,12))
        self.assertEqual(self.stock.name,'椅子')

    def test_stock_duplicate_name_and_delete_keeps_old_returned_loans(self):
        other = FurnitureStock.objects.create(name='桌子', total_count=3)
        self.client.force_login(self.staff)
        self.client.post(f'/furniture/stock/{self.stock.pk}/edit/',
                         {'name': '桌子', 'total_count': 8})
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.name, '椅子')
        self.assertEqual(other.total_count, 3)
        # Existing returned record remains in the original JSON history.
        loan = FurnitureLoan.objects.create(borrower_name='旧借用', student_id='123',
             phone='12345678', items=[{'name':'椅子','quantity':1}], returned_at=timezone.now())
        self.client.post(f'/furniture/stock/{self.stock.pk}/delete/')
        loan.refresh_from_db()
        self.assertEqual(loan.items[0]['name'], '椅子')
        self.assertFalse(FurnitureStock.objects.get(pk=self.stock.pk).is_active)

    def test_quick_booking_reports_errors_in_dialog_and_checks_password_policy(self):
        from django.contrib.auth.hashers import check_password
        from django.test import Client
        day = timezone.localdate() + timedelta(days=2)
        AcademicTerm.objects.create(name='R8学期', starts_on=day-timedelta(days=7),
            ends_on=day+timedelta(days=30), first_week=1, booking_open=True)
        room = Room.objects.create(name='R8A101', building='测试楼')
        period = BorrowPeriod.objects.first()
        self.assertIsNotNone(period)
        data = {
            'date': day.isoformat(), 'period': str(period.pk),
            'room_id': str(room.pk), 'title': '小组活动', 'participants': '4',
            'student_name': '林同学', 'student_id': 'R8student01',
            'student_department': '测试班', 'student_contact': '13800138001',
            'edit_password': 'Qz7p4M', 'edit_password_confirm': 'Qz7p4M',
        }
        bad = self.client.post('/schedule/quick/', {**data, 'title': ''},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json()['ok'])
        self.assertIn('预约用途', bad.json()['message'])
        self.assertEqual(Booking.objects.filter(kind='booking').count(), 0)
        rejected = self.client.post('/schedule/quick/',
            {**data, 'edit_password':'1', 'edit_password_confirm':'1'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(rejected.json()['ok'])
        self.assertFalse(Booking.objects.filter(kind='booking').exists())
        made = self.client.post('/schedule/quick/', data,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(made.status_code, 200)
        self.assertTrue(made.json()['ok'])
        self.assertEqual(made.json()['redirect_url'], '/receipt/')
        self.assertContains(self.client.get('/receipt/'), 'R8A101')
        booked = Booking.objects.get(kind='booking')
        self.assertTrue(check_password('Qz7p4M', booked.edit_password_hash))
        other = Client()
        self.assertEqual(other.post('/lookup/', {'student_id': 'R8student01',
            'password': 'Qz7p4M'}).status_code, 302)
        self.assertContains(other.get('/lookup/'), 'R8A101')
        conflict = self.client.post('/schedule/quick/', {**data, 'student_id': 'R8student02'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(conflict.status_code, 409)
        self.assertFalse(conflict.json()['ok'])
        self.assertEqual(Booking.objects.filter(kind='booking').count(), 1)

    def test_quick_booking_long_password_works_in_lookup(self):
        from django.test import Client
        day = timezone.localdate() + timedelta(days=2)
        AcademicTerm.objects.create(name='R8学期', starts_on=day-timedelta(days=7),
            ends_on=day+timedelta(days=30), first_week=1, booking_open=True)
        room = Room.objects.create(name='R8B102', building='测试楼')
        period = BorrowPeriod.objects.first()
        password = 'r8' * 100
        data = {'date': day.isoformat(), 'period': str(period.pk), 'room_id': str(room.pk),
            'title': '物理实验', 'participants': '2', 'student_name': '赵同学',
            'student_id': 'R8student03', 'student_department': '物理一班',
            'student_contact': '13800138002',
            'edit_password': password, 'edit_password_confirm': password}
        response = self.client.post('/schedule/quick/', data,
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        other = Client()
        self.assertEqual(other.post('/lookup/', {'student_id': 'R8student03',
            'password': password}).status_code, 302)
        self.assertContains(other.get('/lookup/'), 'R8B102')

    def test_quick_booking_popup_submission_and_conflict_prevention(self):
        from datetime import date
        day = timezone.localdate() + timedelta(days=1)
        AcademicTerm.objects.create(
            name='测试学期', starts_on=day-timedelta(days=7),
            ends_on=day+timedelta(days=30), first_week=1, booking_open=True,
        )
        room = Room.objects.create(name='T202', building='南教学楼')
        period = BorrowPeriod.objects.first()
        self.assertIsNotNone(period)
        schedule = self.client.get('/schedule/?view=day&date='+day.isoformat())
        self.assertContains(schedule, 'v3-quick-dialog')
        self.assertContains(schedule, 'data-v3-room-id')
        self.assertNotContains(schedule, 'data-v3-room-name')
        self.assertContains(schedule, room.name, count=5)
        post = {
            'date': day.isoformat(), 'period': period.pk, 'room_id': room.pk,
            'title': '物理研讨', 'participants': '6',
            'student_name': '第一学生', 'student_id': '202620123',
            'student_department': '物理学院', 'student_contact': '13800138001',
            'edit_password': 'UniquePassV3!2026#',
            'edit_password_confirm': 'UniquePassV3!2026#',
            'consent': 'on',
        }
        response = self.client.post('/schedule/quick/', post)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.filter(kind='booking').count(), 1)
        self.client.post('/schedule/quick/', {**post, 'student_id': '202620124'})
        self.assertEqual(Booking.objects.filter(kind='booking').count(), 1)


# Administrator can set a preferred real name for subsequent public loan records.
