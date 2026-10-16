from datetime import timedelta
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import FurnitureLoan, User


@override_settings(RETENTION_DAYS=21)
class FurnitureLoanTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            username="furniture-manager",
            password="manager-test-password!2026",
            is_staff=True,
        )

    def payload(self, **changes):
        data = {
            "borrower_name": "李同学",
            "student_id": "2026123456",
            "phone": "13800138000",
            "purpose": "教室活动",
            "consent": "on",
            "item_name": ["桌子", "椅子"],
            "item_quantity": ["2", "6"],
        }
        data.update(changes)
        return data

    def test_guest_registration_is_private_and_manager_can_mark_returned(self):
        response = self.client.post("/furniture/", self.payload(), follow=True)
        self.assertContains(response, "桌椅借用登记成功")
        loan = FurnitureLoan.objects.get()
        self.assertEqual(
            loan.items,
            [{"name": "桌子", "quantity": 2}, {"name": "椅子", "quantity": 6}],
        )
        self.assertNotContains(response, "13800138000")
        self.assertNotContains(response, "2026123456")
        self.client.post(f"/furniture/{loan.pk}/return/")
        loan.refresh_from_db()
        self.assertIsNone(loan.returned_at)
        self.client.force_login(self.manager)
        response = self.client.get("/furniture/")
        self.assertContains(response, "13800138000")
        self.assertContains(response, "2026123456")
        self.assertEqual(self.client.get(f"/furniture/{loan.pk}/return/").status_code, 405)
        self.client.post(f"/furniture/{loan.pk}/return/")
        loan.refresh_from_db()
        self.assertIsNotNone(loan.returned_at)

    def test_invalid_or_excessive_item_rows_do_not_create_record(self):
        for data in (
            self.payload(item_quantity=["2", ""]),
            self.payload(item_quantity=["0", "6"]),
            self.payload(item_name=["桌子"] * 21, item_quantity=["1"] * 21),
        ):
            with self.subTest(data=data):
                response = self.client.post("/furniture/", data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "errorlist")
        self.assertEqual(FurnitureLoan.objects.count(), 0)

    def test_registration_personal_data_is_purged_within_booking_retention(self):
        self.client.post("/furniture/", self.payload())
        old = FurnitureLoan.objects.get()
        FurnitureLoan.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=22)
        )
        self.client.post("/furniture/", self.payload(borrower_name="新同学"))
        call_command("purge_expired", verbosity=0)
        self.assertFalse(FurnitureLoan.objects.filter(pk=old.pk).exists())
        self.assertEqual(FurnitureLoan.objects.count(), 1)
