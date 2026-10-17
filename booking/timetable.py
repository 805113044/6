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
    cutoff = timezone.localdate() - timedelta(days=settings.BOOKING_RETENTION_DAYS)
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


@transaction.atomic
def release_imported_course_period(user, room_id, day, period_id, teacher_name):
    """Release only one period of an imported teaching occupancy.

    The original occurrence (with its deterministic course_key) remains in
    cancelled state.  This is a durable exception/tombstone: subsequent hourly
    timetable sync and planned_entries will NOT regenerate the released slot.
    Unreleased fragments become separate approved course bookings.
    """
    from .models import AcademicTerm, BorrowPeriod, User
    from .school import term_settings
    import secrets

    if not user.is_authenticated:
        raise PermissionDenied
    staff = bool(user.is_staff)
    teacher = (user.role == User.Role.TEACHER and bool(user.display_name.strip()))
    if not staff and not teacher:
        raise PermissionDenied
    if not teacher_name or len(teacher_name) > 60:
        raise ValidationError("请选择有效的任课教师。")
    if not staff and teacher_name != user.display_name.strip():
        raise PermissionDenied

    term = term_settings(lock=True)
    room = Room.objects.select_for_update().filter(pk=room_id, is_active=True).first()
    period = BorrowPeriod.objects.filter(pk=period_id).first()
    if room is None or period is None:
        raise ValidationError("教室或节次不存在。")
    if not term.starts_on <= day <= term.ends_on:
        raise ValidationError("日期不在当前学期内。")
    start = timezone.make_aware(datetime.combine(day, period.start))
    end = timezone.make_aware(datetime.combine(day, period.end))
    now = timezone.now()
    if end <= now or start > now + timedelta(days=settings.BOOKING_ADVANCE_DAYS):
        raise ValidationError("只能临时开放尚未结束、未来可预约范围内的节次。")

    matched = []
    for rule in TeachingRule.objects.filter(room=room, teacher_name=teacher_name, is_active=True):
        if rule.occurs_on(day):
            rstart, rend = interval(rule, day)
            if rstart < end and rend > start:
                matched.append((rule, rstart, rend))
    if len(matched) != 1:
        raise ValidationError("没有找到唯一匹配的正式课程，或本节已开放。")
    rule, rule_start, rule_end = matched[0]
    key = occurrence_key(rule, day)
    origin = Booking.objects.select_for_update().filter(course_key=key).first()
    if origin is None:
        if Booking.objects.occupying().filter(room=room, start_at__lt=rule_end, end_at__gt=rule_start).exists():
            raise ValidationError("这节课与现有占用发生冲突，无法自动释放。")
        origin = Booking.objects.create(
            room=room, applicant=rule.created_by, title=rule.title,
            teacher_name=rule.teacher_name, start_at=rule_start, end_at=rule_end,
            kind=Booking.Kind.COURSE, status=Booking.Status.APPROVED,
            course_key=key,
        )
        AuditEvent.objects.create(booking=origin, actor=user, action="课表单次展开")

    portions = list(Booking.objects.select_for_update().filter(
        room=room, kind=Booking.Kind.COURSE, teacher_name=teacher_name,
        status__in=[Booking.Status.APPROVED, Booking.Status.PENDING],
        start_at__lt=end, end_at__gt=start,
    ).order_by('pk'))
    if len(portions) != 1:
        raise ValidationError("当前节次没有唯一可释放的教学占用，请刷新课表。")
    piece = portions[0]
    left = max(piece.start_at, start)
    right = min(piece.end_at, end)
    if left >= right:
        raise ValidationError("此节次已没有需要释放的教学占用。")
    original_start, original_end = piece.start_at, piece.end_at
    piece.status = Booking.Status.CANCELLED
    piece.reason = "单次课表临时释放"
    piece.reviewed_by = user
    piece.reviewed_at = now
    piece.save(update_fields=['status', 'reason', 'reviewed_by', 'reviewed_at'])
    fragments = []
    for a, b in [(original_start, left), (right, original_end)]:
        if a >= b:
            continue
        child_key = hashlib.sha256(f"course-segment:{piece.pk}:{a.isoformat()}:{b.isoformat()}".encode()).hexdigest()
        fragment = Booking.objects.create(
            room=room, applicant=piece.applicant, title=piece.title,
            teacher_name=piece.teacher_name, start_at=a, end_at=b,
            kind=Booking.Kind.COURSE, status=Booking.Status.APPROVED,
            course_key=child_key,
        )
        fragments.append(fragment)
        AuditEvent.objects.create(booking=fragment, actor=user, action="保留课程其余节次")
    AuditEvent.objects.create(
        booking=piece, actor=user, action="单节课表临时释放",
        note=f"{day.isoformat()} {period} 释放；其余{len(fragments)}段保留"[:300],
    )
    return piece
