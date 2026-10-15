"""Semester rules stay as configuration; reservation rows keep their 60-day cap."""

import hashlib
import json
from datetime import datetime, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import AuditEvent, Booking, Room, TeachingRule


def occurrence_key(rule, day):
    return hashlib.sha256(f"{rule.key}:{day.isoformat()}".encode()).hexdigest()


def interval(rule, day):
    return (
        timezone.make_aware(datetime.combine(day, rule.start)),
        timezone.make_aware(datetime.combine(day, rule.end)),
    )


def rule_days(rule):
    from .models import AcademicTerm

    term = AcademicTerm.objects.filter(pk=1).first()
    for week in range(rule.week_start, rule.week_end + 1):
        day = rule.term_start + timedelta(weeks=week - 1, days=rule.weekday - 1)
        if rule.occurs_on(day) and (not term or term.starts_on <= day <= term.ends_on):
            yield day


def check_rule_conflicts(room, start, end, exclude=None):
    """Definitive guard even before the hourly materializer reaches a new day."""
    day = timezone.localdate(start)
    from .models import AcademicTerm

    term = AcademicTerm.objects.filter(pk=1).first()
    if term and not term.starts_on <= day <= term.ends_on:
        return
    for rule in TeachingRule.objects.filter(room=room, is_active=True):
        if not rule.occurs_on(day):
            continue
        # A materialized row, including a cancelled/edited exception, is authoritative.
        if (
            Booking.objects.retained()
            .filter(course_key=occurrence_key(rule, day))
            .exists()
        ):
            continue
        a, b = interval(rule, day)
        if a < end and b > start:
            raise ValidationError("此时段属于正式课表占用，请更换时间。")


def materialize_rule(rule):
    now = timezone.now()
    horizon = now + timedelta(days=settings.BOOKING_ADVANCE_DAYS)
    created = skipped = 0
    for day in rule_days(rule):
        start, end = interval(rule, day)
        if end <= now or start > horizon:
            continue
        key = occurrence_key(rule, day)
        if Booking.objects.retained().filter(course_key=key).exists():
            skipped += 1
            continue
        if (
            Booking.objects.occupying()
            .filter(room=rule.room, start_at__lt=end, end_at__gt=start)
            .exists()
        ):
            raise ValidationError(
                f"{rule.room.name} {day} {rule.start:%H:%M} 与已有预约冲突。"
            )
        b = Booking.objects.create(
            created_at=now,
            room=rule.room,
            applicant=rule.created_by,
            title=rule.title,
            teacher_name=rule.teacher_name,
            start_at=start,
            end_at=end,
            kind="course",
            status="approved",
            course_key=key,
        )
        AuditEvent.objects.create(
            booking=b, actor=rule.created_by, action="单双周课表展开"
        )
        created += 1
    return created, skipped


@transaction.atomic
def import_timetable(user, rows, *, dry_run):
    if not user.is_staff:
        raise PermissionDenied
    from .school import term_settings, ensure_import_rooms

    term = term_settings(lock=True)
    ensure_import_rooms(rows)
    rooms = {
        r.name: r
        for r in Room.objects.select_for_update()
        .filter(name__in={r["room"] for r in rows})
        .order_by("pk")
    }
    created = skipped = 0
    now = timezone.now()
    for number, row in enumerate(rows, 2):
        if row["room"] not in rooms:
            raise ValidationError(f"第 {number} 行教室不存在。")
        data = dict(row["rule"])
        data["teacher_name"] = data.pop("teacher")
        room = rooms[row["room"]]
        key = hashlib.sha256(
            json.dumps({"room": room.pk, **data}, sort_keys=True, default=str).encode()
        ).hexdigest()
        rule = TeachingRule(room=room, key=key, created_by=user, **data)
        if rule.term_start != term.week_one_monday:
            raise ValidationError("课表周次与当前学期设置不一致。")
        future_days = [d for d in rule_days(rule) if interval(rule, d)[1] > now]
        if not future_days:
            skipped += 1
            continue
        existing = TeachingRule.objects.filter(key=key).first()
        if existing:
            skipped += 1
            continue
        others = list(TeachingRule.objects.filter(room=room, is_active=True))
        for other in others:
            if (
                other.start < rule.end
                and other.end > rule.start
                and any(other.occurs_on(d) for d in future_days)
            ):
                raise ValidationError(
                    f"第 {number} 行与已导入课表规则冲突（{room.name}）。"
                )
        rule.save()
        materialize_rule(rule)
        created += 1
    if dry_run:
        transaction.set_rollback(True)
    return created, skipped


def sync_timetables():
    from .models import AcademicTerm

    if not AcademicTerm.objects.filter(pk=1).exists():
        return
    for pk in (
        TeachingRule.objects.filter(is_active=True)
        .values_list("pk", flat=True)
        .iterator()
    ):
        with transaction.atomic():
            from .school import term_settings

            term_settings(lock=True)
            initial = TeachingRule.objects.get(pk=pk)
            Room.objects.select_for_update().get(pk=initial.room_id)
            rule = TeachingRule.objects.select_for_update().get(pk=pk)
            if rule.is_active:
                materialize_rule(rule)
    cutoff = timezone.localdate() - timedelta(days=settings.RETENTION_DAYS)
    for rule in TeachingRule.objects.all().iterator():
        if rule.term_start + timedelta(weeks=rule.week_end) < cutoff:
            rule.delete()


def planned_entries(room_ids, days):
    """Show later semester weeks truthfully without storing long-lived bookings."""
    from .models import AcademicTerm

    term = AcademicTerm.objects.filter(pk=1).first()
    candidates = []
    for rule in TeachingRule.objects.filter(
        room_id__in=room_ids, is_active=True
    ).select_related("room"):
        for day in days:
            if rule.occurs_on(day) and (
                not term or term.starts_on <= day <= term.ends_on
            ):
                start, end = interval(rule, day)
                if end > timezone.now():
                    candidates.append((rule, start, end, occurrence_key(rule, day)))
    existing = set(
        Booking.objects.retained()
        .filter(course_key__in=[x[3] for x in candidates])
        .values_list("course_key", flat=True)
    )
    return [
        Booking(
            room=rule.room,
            start_at=start,
            end_at=end,
            kind="course",
            status="approved",
            title=rule.title,
            teacher_name=rule.teacher_name,
        )
        for rule, start, end, key in candidates
        if key not in existing
    ]


@transaction.atomic
def stop_rule(user, pk, reason):
    if not user.is_staff:
        raise PermissionDenied
    if not reason.strip() or len(reason) > 300:
        raise ValidationError("停用课表规则须填写原因，最多 300 字。")
    rule = TeachingRule.objects.get(pk=pk)
    Room.objects.select_for_update().get(pk=rule.room_id)
    rule.is_active = False
    rule.save(update_fields=["is_active"])
    keys = [occurrence_key(rule, day) for day in rule_days(rule)]
    for b in Booking.objects.retained().filter(
        course_key__in=keys,
        start_at__gt=timezone.now(),
        status__in=["approved", "pending"],
    ):
        b.status, b.reason = "cancelled", reason
        b.save(update_fields=["status", "reason"])
        AuditEvent.objects.create(
            booking=b, actor=user, action="停用课表规则", note=reason
        )
