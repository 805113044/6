"""Preserve future pre-v3 date classes when concrete rows get a shorter TTL."""

import hashlib
import json
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db import migrations
from django.utils import timezone


def preserve(apps, schema_editor):
    Booking = apps.get_model("booking", "Booking")
    Rule = apps.get_model("booking", "TeachingRule")
    for b in Booking.objects.filter(
        kind="course",
        status__in=["approved", "pending"],
        course_key__isnull=True,
        end_at__gt=timezone.now(),
    ).iterator():
        start, end = (
            b.start_at.astimezone(ZoneInfo("Asia/Shanghai")),
            b.end_at.astimezone(ZoneInfo("Asia/Shanghai")),
        )
        if start.date() != end.date():
            continue
        anchor = start.date() - timedelta(days=start.weekday())
        data = {
            "title": b.title,
            "teacher_name": b.teacher_name,
            "term_start": anchor,
            "week_start": 1,
            "week_end": 1,
            "week_pattern": "all",
            "weekday": start.isoweekday(),
            "start": start.time(),
            "end": end.time(),
            "excluded_dates": [],
        }
        key = hashlib.sha256(
            json.dumps(
                {"room": b.room_id, **data}, sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        rule, _ = Rule.objects.get_or_create(
            key=key,
            defaults={"room_id": b.room_id, "created_by_id": b.applicant_id, **data},
        )
        course_key = hashlib.sha256(
            f"{rule.key}:{start.date().isoformat()}".encode()
        ).hexdigest()
        Booking.objects.filter(pk=b.pk).update(course_key=course_key)


class Migration(migrations.Migration):
    dependencies = [("booking", "0006_seed_five_periods")]
    operations = [migrations.RunPython(preserve, migrations.RunPython.noop)]
