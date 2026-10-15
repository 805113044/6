from datetime import timedelta
import secrets
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from .models import AuditEvent, Booking, Room

_DUMMY_PASSWORD_HASH = make_password(secrets.token_urlsafe(32))


def accessible_rooms(user):
    return Room.objects.all()


def managed_rooms(user):
    if not user.is_authenticated:
        return Room.objects.none()
    if user.is_staff:
        return Room.objects.all()
    return Room.objects.none()


def can_view_details(user):
    return user.is_authenticated and user.is_staff


def guest_owns(request, booking):
    grant = request.session.get("booking_grant", {})
    return (
        booking.applicant_id is None
        and grant.get("pk") == booking.pk
        and grant.get("version") == booking.credential_version
        and grant.get("until", 0) > timezone.now().timestamp()
    )


def grant_guest(request, booking):
    request.session.cycle_key()
    request.session["booking_grant"] = {
        "pk": booking.pk,
        "version": booking.credential_version,
        "until": (timezone.now() + timedelta(minutes=15)).timestamp(),
    }


def verify_guest(booking, student_id, password):
    # Always perform a real password hash check, including unknown/legacy records.
    hashed = (
        booking.edit_password_hash
        if booking and booking.edit_password_hash
        else _DUMMY_PASSWORD_HASH
    )
    matched = check_password(password, hashed)
    return bool(
        booking
        and booking.applicant_id is None
        and matched
        and secrets.compare_digest(booking.student_id, student_id.strip())
    )


def validate_interval(room, start_at, end_at, participants, *, course=False):
    now = timezone.now()
    start, end = timezone.localtime(start_at), timezone.localtime(end_at)
    if not room.is_active:
        raise ValidationError("该教室暂未开放。")
    if start_at <= now or start_at > now + timedelta(
        days=settings.BOOKING_ADVANCE_DAYS
    ):
        raise ValidationError(
            f"请选择未来 {settings.BOOKING_ADVANCE_DAYS} 天内的时间。"
        )
    if end_at <= start_at or start.date() != end.date():
        raise ValidationError("结束时间须晚于开始时间，且必须为同一天。")
    if (
        start.hour < 7
        or end.hour > 22
        or (end.hour == 22 and (end.minute or end.second))
    ):
        raise ValidationError("教室开放时间为 07:00–22:00。")
    if not course and end_at - start_at > timedelta(hours=settings.BOOKING_MAX_HOURS):
        raise ValidationError(f"单次预约不得超过 {settings.BOOKING_MAX_HOURS} 小时。")
    if not 1 <= participants <= room.capacity:
        raise ValidationError(f"使用人数须为 1–{room.capacity} 人。")


def check_overlap(room, start, end, exclude=None):
    occupied = Booking.objects.occupying().filter(
        room=room, start_at__lt=end, end_at__gt=start
    )
    if exclude:
        occupied = occupied.exclude(pk=exclude)
    if occupied.exists():
        raise ValidationError("此时段已有教学占用或预约（含待审批），请更换时间。")
    from .timetable import check_rule_conflicts

    check_rule_conflicts(room, start, end, exclude)


@transaction.atomic
def create_booking(
    user, room_id, *, title, start_at, end_at, participants, student=None
):
    # Every create/review/cancel/import takes the same room lock first.
    if user.is_authenticated and not user.is_staff:
        raise PermissionDenied
    room = Room.objects.select_for_update().get(pk=room_id)
    if not accessible_rooms(user).filter(pk=room.pk).exists():
        raise PermissionDenied
    title = title.strip()
    if not title or len(title) > 100:
        raise ValidationError("请填写 1–100 字的预约用途。")
    validate_interval(room, start_at, end_at, participants)
    check_overlap(room, start_at, end_at)
    code = ""
    extra = {}
    if not user.is_authenticated:
        if not student or any(
            not student.get(k, "").strip()
            for k in (
                "student_name",
                "student_id",
                "student_department",
                "student_contact",
            )
        ):
            raise ValidationError("请完整填写学生姓名、学号、学院和联系方式。")
        limits = {
            "student_name": 60,
            "student_id": 40,
            "student_department": 80,
            "student_contact": 100,
        }
        if any(len(student[k]) > limit for k, limit in limits.items()):
            raise ValidationError("学生信息超过允许长度。")
        password = student.get("edit_password", "")
        validate_password(password)
        extra = {k: student[k].strip() for k in limits}
        extra["edit_password_hash"] = make_password(password)
    booking = Booking.objects.create(
        room=room,
        applicant=user if user.is_authenticated else None,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        status="approved",
        **extra,
    )
    AuditEvent.objects.create(
        booking=booking,
        actor=user if user.is_authenticated else None,
        action="直接预约",
    )
    return booking, code


@transaction.atomic
def decide(user, pk, decision, reason=""):
    initial = Booking.objects.retained().get(pk=pk)
    Room.objects.select_for_update().get(pk=initial.room_id)
    booking = Booking.objects.select_for_update().get(pk=pk)
    if booking.room_id != initial.room_id:
        raise ValidationError("教室刚被更改，请刷新后重试。")
    if not managed_rooms(user).filter(pk=booking.room_id).exists():
        raise PermissionDenied
    if decision not in (
        Booking.Status.APPROVED,
        Booking.Status.REJECTED,
        Booking.Status.RETURNED,
    ):
        raise ValidationError("无效的审批动作。")
    allowed = (
        booking.status in ("approved", "pending")
        if decision == "returned"
        else booking.status == "pending"
    )
    if not allowed:
        raise ValidationError("该状态不能执行此操作，请刷新页面。")
    reason = reason.strip()
    if len(reason) > 300 or (decision in ("rejected", "returned") and not reason):
        raise ValidationError("驳回 / 退回须填写原因，最多 300 字。")
    if booking.start_at <= timezone.now():
        raise ValidationError("开始时间已过，请撤销此申请。")
    if decision == Booking.Status.APPROVED:
        validate_interval(
            booking.room, booking.start_at, booking.end_at, booking.participants
        )
        check_overlap(
            booking.room, booking.start_at, booking.end_at, exclude=booking.pk
        )
    booking.status, booking.reason = decision, reason
    booking.reviewed_by, booking.reviewed_at = user, timezone.now()
    booking.save(update_fields=["status", "reason", "reviewed_by", "reviewed_at"])
    AuditEvent.objects.create(
        booking=booking, actor=user, action=booking.get_status_display(), note=reason
    )
    return booking


@transaction.atomic
def cancel_booking(
    user, pk, reason="", *, guest_authorized=False, expected_version=None
):
    initial = Booking.objects.retained().get(pk=pk)
    Room.objects.select_for_update().get(pk=initial.room_id)
    booking = Booking.objects.select_for_update().get(pk=pk)
    if guest_authorized and expected_version != booking.credential_version:
        raise PermissionDenied
    if booking.room_id != initial.room_id:
        raise ValidationError("教室刚被更改，请刷新后重试。")
    is_manager = managed_rooms(user).filter(pk=booking.room_id).exists()
    is_owner = (user.is_authenticated and booking.applicant_id == user.pk) or (
        not booking.applicant_id and guest_authorized
    )
    if not is_owner and not is_manager:
        raise PermissionDenied
    if booking.status not in (Booking.Status.PENDING, Booking.Status.APPROVED):
        raise ValidationError("此记录已结束处理，不能再次取消。")
    if not is_manager and booking.start_at <= timezone.now() + timedelta(
        minutes=settings.CANCEL_BEFORE_MINUTES
    ):
        raise ValidationError(
            f"距开始不足 {settings.CANCEL_BEFORE_MINUTES} 分钟，请联系管理员。"
        )
    reason = reason.strip()
    if len(reason) > 300 or (
        is_manager and booking.applicant_id != user.pk and not reason
    ):
        raise ValidationError("管理员撤销他人的预约须填写原因（最多 300 字）。")
    booking.status, booking.reason = Booking.Status.CANCELLED, reason
    booking.save(update_fields=["status", "reason"])
    AuditEvent.objects.create(
        booking=booking,
        actor=user if user.is_authenticated else None,
        action="取消 / 撤销",
        note=reason,
    )
    return booking


@transaction.atomic
def edit_booking(
    user,
    pk,
    *,
    room_id,
    title,
    start_at,
    end_at,
    participants,
    student=None,
    guest_authorized=False,
    reason="",
    expected_version=None,
):
    initial = Booking.objects.retained().get(pk=pk)
    locked = {
        r.pk: r
        for r in Room.objects.select_for_update()
        .filter(pk__in=sorted({initial.room_id, room_id}))
        .order_by("pk")
    }
    booking = Booking.objects.select_for_update().get(pk=pk)
    if guest_authorized and expected_version != booking.credential_version:
        raise PermissionDenied
    if booking.room_id != initial.room_id:
        raise ValidationError("此记录刚被其他人修改，请刷新后重试。")
    admin = managed_rooms(user).filter(pk=booking.room_id).exists()
    owner = (user.is_authenticated and booking.applicant_id == user.pk) or (
        booking.applicant_id is None and guest_authorized
    )
    if not admin and not owner:
        raise PermissionDenied
    if booking.kind == "course" and not admin:
        raise PermissionDenied
    if booking.status not in ("approved", "pending", "returned"):
        raise ValidationError("已取消或驳回的记录不能编辑，请重新预约。")
    if not admin and booking.start_at <= timezone.now() + timedelta(
        minutes=settings.CANCEL_BEFORE_MINUTES
    ):
        raise ValidationError("临近使用或已开始，请联系管理员修改。")
    room = locked.get(room_id)
    if not room or not accessible_rooms(user).filter(pk=room_id).exists():
        raise PermissionDenied
    if not title.strip() or len(title) > 100:
        raise ValidationError("用途须为 1–100 字。")
    if admin and (not reason.strip() or len(reason) > 300):
        raise ValidationError("管理员修改须填写原因（最多 300 字）。")
    validate_interval(
        room, start_at, end_at, participants, course=booking.kind == "course"
    )
    check_overlap(room, start_at, end_at, exclude=pk)
    # Keep a masked operation note: snapshots containing student PII are unnecessary.
    changed = [
        f"{booking.room.name} {timezone.localtime(booking.start_at):%m-%d %H:%M} → {room.name} {timezone.localtime(start_at):%m-%d %H:%M}"
    ]
    (
        booking.room,
        booking.title,
        booking.start_at,
        booking.end_at,
        booking.participants,
    ) = room, title.strip(), start_at, end_at, participants
    if student is not None and booking.applicant_id is None:
        for name, limit in {
            "student_name": 60,
            "student_id": 40,
            "student_department": 80,
            "student_contact": 100,
        }.items():
            value = student.get(name, "").strip()
            if not value or len(value) > limit:
                raise ValidationError("请完整填写长度有效的学生信息。")
            setattr(booking, name, value)
        new_password = student.get("edit_password", "")
        if new_password:
            validate_password(new_password)
            booking.edit_password_hash = make_password(new_password)
        booking.credential_version += 1
        # Revoke old lookup capabilities when identity information changes.
        booking.lookup_hash = None
    booking.status, booking.reason = "approved", reason.strip() if admin else ""
    booking.save()
    AuditEvent.objects.create(
        booking=booking,
        actor=user if user.is_authenticated else None,
        action="管理员编辑" if admin else "本人修改",
        note=(reason + "；" + changed[0])[:300],
    )
    return booking


@transaction.atomic
def import_course_rows(user, rows, *, dry_run):
    from .timetable import import_timetable

    if any("rule" in row for row in rows):
        return import_timetable(user, rows, dry_run=dry_run)
    if not user.is_staff:
        raise PermissionDenied
    names = sorted({r["room"] for r in rows})
    rooms = {
        r.name: r
        for r in Room.objects.select_for_update().filter(name__in=names).order_by("pk")
    }
    allowed = set(managed_rooms(user).values_list("pk", flat=True))
    created = skipped = 0
    for number, row in enumerate(rows, 2):
        room = rooms.get(row["room"])
        if not room or room.pk not in allowed:
            raise ValidationError(f"第 {number} 行：教室不存在或无管理权限。")
        try:
            validate_interval(room, row["start_at"], row["end_at"], 1, course=True)
            if (
                Booking.objects.occupying()
                .filter(
                    room=room,
                    kind="course",
                    status="approved",
                    title=row["title"],
                    start_at=row["start_at"],
                    end_at=row["end_at"],
                )
                .exists()
            ):
                skipped += 1
                continue
            check_overlap(room, row["start_at"], row["end_at"])
        except ValidationError as exc:
            raise ValidationError(f"第 {number} 行：{'；'.join(exc.messages)}") from exc
        booking = Booking.objects.create(
            room=room,
            applicant=user,
            kind="course",
            status="approved",
            title=row["title"],
            start_at=row["start_at"],
            end_at=row["end_at"],
            reviewed_by=user,
            reviewed_at=timezone.now(),
            teacher_name=row.get("teacher", ""),
        )
        AuditEvent.objects.create(booking=booking, actor=user, action="导入教学占用")
        created += 1
    if dry_run:
        transaction.set_rollback(True)
    return created, skipped
