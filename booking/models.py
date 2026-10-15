from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "学生"
        TEACHER = "teacher", "教师"

    display_name = models.CharField("姓名", max_length=60)
    department = models.CharField("学院 / 部门", max_length=80, blank=True)
    role = models.CharField(
        "身份", max_length=12, choices=Role.choices, default=Role.STUDENT
    )

    def __str__(self):
        return self.display_name or self.username


class Room(models.Model):
    name = models.CharField("教室编号", max_length=40, unique=True)
    campus = models.CharField("校区", max_length=60, default="主校区")
    building = models.CharField("教学楼", max_length=60)
    floor = models.PositiveSmallIntegerField("楼层", default=1)
    capacity = models.PositiveSmallIntegerField(
        "容纳人数", validators=[MinValueValidator(1), MaxValueValidator(2000)]
    )
    room_type = models.CharField(
        "类型",
        max_length=30,
        choices=[
            ("lecture", "多媒体教室"),
            ("seminar", "研讨教室"),
            ("lab", "实验教室"),
        ],
        default="lecture",
    )
    equipment = models.CharField(
        "设备", max_length=200, blank=True, help_text="例如：投影仪、白板、空调"
    )
    department = models.CharField(
        "所属学院 / 部门",
        max_length=80,
        blank=True,
        help_text="留空表示全校共享；非空时仅限同部门用户及系统管理员",
    )
    is_active = models.BooleanField("开放预约", default=True)

    class Meta:
        ordering = ["campus", "building", "name"]
        verbose_name = "教室"
        verbose_name_plural = "教室"

    def __str__(self):
        return f"{self.building} · {self.name}"


class BookingQuerySet(models.QuerySet):
    def retained(self):
        return self.filter(
            created_at__gt=timezone.now() - timedelta(days=settings.RETENTION_DAYS)
        )

    def occupying(self):
        return self.retained().filter(status__in=["pending", "approved"])


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待审批"
        APPROVED = "approved", "已批准"
        REJECTED = "rejected", "已驳回"
        CANCELLED = "cancelled", "已取消"

    class Kind(models.TextChoices):
        BOOKING = "booking", "预约"
        COURSE = "course", "教学占用"

    room = models.ForeignKey(Room, on_delete=models.PROTECT, verbose_name="教室")
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bookings",
        verbose_name="申请人",
        null=True,
        blank=True,
    )
    student_name = models.CharField("学生姓名", max_length=60, blank=True)
    student_id = models.CharField("学号", max_length=40, blank=True)
    student_department = models.CharField("学院 / 班级", max_length=80, blank=True)
    student_contact = models.CharField("联系方式", max_length=100, blank=True)
    lookup_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False
    )
    title = models.CharField("用途 / 课程", max_length=100)
    participants = models.PositiveSmallIntegerField(
        "使用人数", default=1, validators=[MinValueValidator(1)]
    )
    start_at = models.DateTimeField("开始时间")
    end_at = models.DateTimeField("结束时间")
    status = models.CharField(
        "状态", max_length=12, choices=Status.choices, default=Status.PENDING
    )
    kind = models.CharField(
        "来源", max_length=10, choices=Kind.choices, default=Kind.BOOKING
    )
    created_at = models.DateTimeField("提交时间", default=timezone.now, editable=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField("处理意见", max_length=300, blank=True)
    objects = BookingQuerySet.as_manager()

    @property
    def applicant_label(self):
        return str(self.applicant) if self.applicant_id else self.student_name

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["room", "status", "start_at", "end_at"]),
            models.Index(fields=["created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F("start_at")),
                name="booking_positive_duration",
            ),
            models.CheckConstraint(
                condition=models.Q(participants__gte=1),
                name="booking_positive_participants",
            ),
        ]
        verbose_name = "预约 / 教学占用"
        verbose_name_plural = "预约与教学占用（只读）"


class AuditEvent(models.Model):
    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    action = models.CharField(max_length=40)
    created_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True)


class LoginAttempt(models.Model):
    # Hash of username; never persist passwords or raw IP addresses.
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)
