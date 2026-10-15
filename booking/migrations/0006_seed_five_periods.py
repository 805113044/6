from datetime import time
from zoneinfo import ZoneInfo
from django.db import migrations


def seed(apps, schema_editor):
    Period = apps.get_model("booking", "BorrowPeriod")
    slots = [
        (time(8), time(9, 40)),
        (time(10), time(11, 40)),
        (time(14), time(15, 40)),
        (time(16), time(17, 40)),
        (time(19), time(21)),
    ]
    mapping = {}
    for number, (start, end) in enumerate(slots, 1):
        p, _ = Period.objects.get_or_create(
            number=number, defaults={"start": start, "end": end}
        )
        mapping[(start, end)] = p.pk
    apps.get_model("booking", "Room").objects.filter(room_type="seminar").update(
        room_type="lecture"
    )
    Booking = apps.get_model("booking", "Booking")
    for b in Booking.objects.filter(kind="booking").iterator():
        a, b_end = (
            b.start_at.astimezone(ZoneInfo("Asia/Shanghai")),
            b.end_at.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        if a.date() == b_end.date() and (a.time(), b_end.time()) in mapping:
            Booking.objects.filter(pk=b.pk).update(
                period_id=mapping[(a.time(), b_end.time())]
            )


class Migration(migrations.Migration):
    dependencies = [("booking", "0005_alter_room_capacity_alter_room_floor_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
