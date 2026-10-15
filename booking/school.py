from datetime import datetime, timedelta
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import AcademicTerm, BorrowPeriod, Booking, Room, TeachingRule


def term_settings(*, lock=False):
    qs = AcademicTerm.objects.select_for_update() if lock else AcademicTerm.objects
    term = qs.filter(pk=1).first()
    if term is None:
        raise ValidationError("管理员尚未配置本学期，请先完成学期设置。")
    return term


def period_for(start, end):
    a, b = timezone.localtime(start), timezone.localtime(end)
    return (
        BorrowPeriod.objects.filter(start=a.time(), end=b.time()).first()
        if a.date() == b.date()
        else None
    )


def check_term_date(term, day, *, course=False):
    if not course and not term.booking_open:
        raise ValidationError("管理员已暂停本学期借用。")
    if not term.starts_on <= day <= term.ends_on:
        raise ValidationError("所选日期不在本学期起止范围内。")


@transaction.atomic
def save_school(user, data):
    if not user.is_staff:
        raise PermissionDenied
    term = AcademicTerm.objects.select_for_update().filter(pk=1).first()
    proposed = AcademicTerm(
        pk=1,
        name=data["name"],
        starts_on=data["starts_on"],
        ends_on=data["ends_on"],
        first_week=data["first_week"],
        booking_open=data["booking_open"],
    )
    proposed.full_clean(
        exclude=["id"], validate_constraints=False, validate_unique=False
    )
    future = Booking.objects.occupying().filter(
        kind="booking", end_at__gt=timezone.now()
    )
    if future.filter(
        Q(
            start_at__lt=timezone.make_aware(
                datetime.combine(proposed.starts_on, datetime.min.time())
            )
        )
        | Q(
            end_at__gte=timezone.make_aware(
                datetime.combine(
                    proposed.ends_on + timedelta(days=1), datetime.min.time()
                )
            )
        )
    ).exists():
        raise ValidationError(
            "已有未来借用超出新的学期范围，请先处理这些预约，或仅关闭借用开关。"
        )
    if (
        term
        and (
            term.starts_on != proposed.starts_on
            or term.first_week != proposed.first_week
        )
        and TeachingRule.objects.filter(is_active=True).exists()
    ):
        raise ValidationError(
            "已有生效的正式课表。修改起始日期/周数前请先停用旧课表，再按新学期重新导入。"
        )
    periods = list(BorrowPeriod.objects.select_for_update().all())
    if len(periods) != 5:
        raise ValidationError("五个借用节次尚未初始化完整，请先运行数据库迁移。")
    previous_end = None
    for p in periods:
        start, end = data[f"p{p.number}_start"], data[f"p{p.number}_end"]
        if start >= end or (previous_end is not None and start < previous_end):
            raise ValidationError("五节须按顺序排列，开始早于结束，且时段不能重叠。")
        if (p.start != start or p.end != end) and future.filter(period=p).exists():
            raise ValidationError(f"{p}已有未来借用，调整时间前请先处理对应预约。")
        p.start, p.end = start, end
        p.save(update_fields=["start", "end"])
        previous_end = end
    if term:
        for field in ("name", "starts_on", "ends_on", "first_week", "booking_open"):
            setattr(term, field, getattr(proposed, field))
        term.save()
    else:
        proposed.save()
    return proposed


def ensure_import_rooms(rows):
    """Called inside import transactions; preview/conflict rolls back new rooms too."""
    seen = {}
    for row in rows:
        name = row["room"].strip()
        if not name or len(name) > 40:
            raise ValidationError("教室编号须为 1–40 字。")
        meta = row.get("room_meta", {})
        if name in seen and any(
            k in seen[name] and seen[name][k] != v for k, v in meta.items()
        ):
            raise ValidationError(f"{name}的教室资料在文件中不一致。")
        seen.setdefault(name, {}).update(meta)
    for name in sorted(seen):
        meta = seen[name]
        Room.objects.get_or_create(
            name=name,
            defaults={
                "campus": meta.get("campus", "未分类"),
                "building": meta.get("building", "未分类"),
                "floor": meta.get("floor"),
                "capacity": meta.get("capacity"),
                "room_type": meta.get("room_type", "lecture"),
            },
        )


def availability(rooms, day, periods, *, include_entries=True):
    """Same exact overlap semantics for cards, grids and submit-time validation."""
    from django.conf import settings
    from .timetable import planned_entries

    start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    entries = list(
        Booking.objects.occupying()
        .filter(
            room_id__in=[r.pk for r in rooms],
            start_at__lt=start + timedelta(days=1),
            end_at__gt=start,
        )
        .select_related("applicant", "period")
    )
    entries.extend(planned_entries([r.pk for r in rooms], [day]))
    term = AcademicTerm.objects.filter(pk=1).first()
    now = timezone.now()
    result = {}
    for room in rooms:
        cells = []
        for p in periods:
            a = timezone.make_aware(datetime.combine(day, p.start))
            b = timezone.make_aware(datetime.combine(day, p.end))
            overlaps = [
                e
                for e in entries
                if e.room_id == room.pk and e.start_at < b and e.end_at > a
            ]
            open_slot = bool(
                term
                and term.booking_open
                and term.starts_on <= day <= term.ends_on
                and now < a <= now + timedelta(days=settings.BOOKING_ADVANCE_DAYS)
            )
            cells.append(
                {
                    "period": p,
                    "entries": overlaps if include_entries else [],
                    "free": open_slot and room.is_active and not overlaps,
                }
            )
        result[room.pk] = cells
    return result
