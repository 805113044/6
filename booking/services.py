from datetime import timedelta
import hashlib
import secrets
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import AuditEvent, Booking, Room


def accessible_rooms(user):
    if not user.is_authenticated:
        return Room.objects.filter(department="")
    if user.is_superuser:
        return Room.objects.all()
    scope = Q(department="")
    if user.department:
        scope |= Q(department=user.department)
    return Room.objects.filter(scope)


def managed_rooms(user):
    if not user.is_authenticated:
        return Room.objects.none()
    if user.is_superuser:
        return Room.objects.all()
    if user.is_staff and user.department:
        return Room.objects.filter(department=user.department)
    return Room.objects.none()


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


@transaction.atomic
def create_booking(
    user, room_id, *, title, start_at, end_at, participants, student=None
):
    # Every create/review/cancel/import takes the same room lock first.
    room = Room.objects.select_for_update().get(pk=room_id)
    if not accessible_rooms(user).filter(pk=room.pk).exists():
        raise PermissionDenied
    title = title.strip()
    if not title or len(title) > 100:
        raise ValidationError("请填写 1–100 字的预约用途。")
    validate_interval(room, start_at, end_at, participants)
    check_overlap(room, start_at, end_at)
    is_teacher = user.is_authenticated and (user.role == "teacher" or user.is_staff)
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
        code = secrets.token_urlsafe(32)
        extra = {k: student[k].strip() for k in limits}
        extra["lookup_hash"] = hashlib.sha256(code.encode()).hexdigest()
    booking = Booking.objects.create(
        room=room,
        applicant=user if user.is_authenticated else None,
        title=title,
        start_at=start_at,
        end_at=end_at,
        participants=participants,
        status="approved" if is_teacher else "pending",
        **extra,
    )
    AuditEvent.objects.create(
        booking=booking,
        actor=user if user.is_authenticated else None,
        action="教师直接预约" if is_teacher else "提交预约",
    )
    return booking, code


@transaction.atomic
def decide(user, pk, decision, reason=""):
    initial = Booking.objects.retained().get(pk=pk)
    Room.objects.select_for_update().get(pk=initial.room_id)
    booking = Booking.objects.select_for_update().get(pk=pk)
    if not managed_rooms(user).filter(pk=booking.room_id).exists():
        raise PermissionDenied
    if booking.status != Booking.Status.PENDING:
        raise ValidationError("该申请已处理，请刷新页面。")
    if decision not in (Booking.Status.APPROVED, Booking.Status.REJECTED):
        raise ValidationError("无效的审批动作。")
    reason = reason.strip()
    if len(reason) > 300 or (decision == Booking.Status.REJECTED and not reason):
        raise ValidationError("驳回须填写原因，处理意见最多 300 字。")
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
def cancel_booking(user, pk, reason="", *, guest_authorized=False):
    initial = Booking.objects.retained().get(pk=pk)
    Room.objects.select_for_update().get(pk=initial.room_id)
    booking = Booking.objects.select_for_update().get(pk=pk)
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
def import_course_rows(user, rows, *, dry_run):
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
        )
        AuditEvent.objects.create(booking=booking, actor=user, action="导入教学占用")
        created += 1
    if dry_run:
        transaction.set_rollback(True)
    return created, skipped
