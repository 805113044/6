from datetime import timedelta
from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from booking.models import Booking, FurnitureLoan, LoginAttempt


def purge():
    cutoff = timezone.now() - timedelta(days=settings.RETENTION_DAYS)
    total = 0
    # Small transactions keep the cleanup from locking large tables for long.
    while True:
        ids = list(
            Booking.objects.filter(created_at__lte=cutoff).values_list("pk", flat=True)[
                :500
            ]
        )
        if not ids:
            break
        with transaction.atomic():
            count, _ = Booking.objects.filter(pk__in=ids).delete()
            total += count
    while True:
        ids = list(
            FurnitureLoan.objects.filter(created_at__lte=cutoff).values_list(
                "pk", flat=True
            )[:500]
        )
        if not ids:
            break
        with transaction.atomic():
            count, _ = FurnitureLoan.objects.filter(pk__in=ids).delete()
            total += count
    LoginAttempt.objects.filter(
        updated_at__lt=timezone.now() - timedelta(days=1)
    ).delete()
    Session.objects.filter(expire_date__lt=timezone.now()).delete()
    return total


class Command(BaseCommand):
    help = "清除超出保留期的预约、桌椅登记和学生信息；清除过期会话与限流计数。"

    def handle(self, *args, **options):
        self.stdout.write(f"已清理 {purge()} 条过期登记及关联记录。")
