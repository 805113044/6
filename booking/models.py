from datetime import timedelta
from django.core.exceptions import ValidationError
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
    floor = models.PositiveSmallIntegerField("楼层", null=True, blank=True)
    capacity = models.PositiveSmallIntegerField(
        "容纳人数",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(2000)],
    )
    room_type = models.CharField(
        "类型",
        max_length=30,
        choices=[
            ("lecture", "教室"),
            ("lab", "实验室"),
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
        help_text="教室归属资料，不限制普通用户借用；所有开放教室均可预约。",
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
        RETURNED = "returned", "已退回"

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
    student_name = models.CharField("借用人姓名", max_length=60, blank=True)
    student_id = models.CharField("学号 / 工号", max_length=40, blank=True)
    student_department = models.CharField("学院 / 班级", max_length=80, blank=True)
    student_contact = models.CharField("联系方式", max_length=100, blank=True)
    lookup_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False
    )
    edit_password_hash = models.CharField(max_length=128, blank=True, editable=False)
    credential_version = models.PositiveIntegerField(default=1, editable=False)
    teacher_name = models.CharField("任课教师", max_length=60, blank=True)
    course_key = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False
    )
    period = models.ForeignKey(
        "BorrowPeriod",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        verbose_name="借用节次",
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
        if self.kind == self.Kind.COURSE:
            return self.teacher_name or "教学安排"
        return str(self.applicant) if self.applicant_id else self.student_name

    @property
    def period_label(self):
        if self.period_id:
            return str(self.period)
        # Historic arbitrary-time reservations still block every overlapping period.
        local_start, local_end = (
            timezone.localtime(self.start_at),
            timezone.localtime(self.end_at),
        )
        labels = [
            str(p)
            for p in BorrowPeriod.objects.all()
            if p.start < local_end.time() and p.end > local_start.time()
        ]
        return "、".join(labels) or "非借用节次课程"

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


class TeachingRule(models.Model):
    """Non-student timetable configuration; concrete occupancies roll forward."""

    key = models.CharField(max_length=64, unique=True, editable=False)
    room = models.ForeignKey(Room, on_delete=models.PROTECT)
    title = models.CharField(max_length=100)
    teacher_name = models.CharField(max_length=60)
    term_start = models.DateField(help_text="第一教学周的周一")
    week_start = models.PositiveSmallIntegerField()
    week_end = models.PositiveSmallIntegerField()
    week_pattern = models.CharField(
        max_length=8, choices=[("all", "每周"), ("odd", "单周"), ("even", "双周")]
    )
    weekday = models.PositiveSmallIntegerField()
    start = models.TimeField()
    end = models.TimeField()
    excluded_dates = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )

    def occurs_on(self, day):
        delta = (day - self.term_start).days
        week = delta // 7 + 1
        return (
            delta >= 0
            and self.week_start <= week <= self.week_end
            and day.isoweekday() == self.weekday
            and (
                self.week_pattern == "all"
                or week % 2 == (1 if self.week_pattern == "odd" else 0)
            )
            and day.isoformat() not in self.excluded_dates
        )


class AcademicTerm(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    name = models.CharField("学期名称", max_length=80)
    starts_on = models.DateField("学期开始日期")
    ends_on = models.DateField("学期结束日期")
    first_week = models.PositiveSmallIntegerField(
        "起始周数", default=1, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    booking_open = models.BooleanField("开放本学期借用", default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name="one_academic_term")
        ]

    @property
    def week_one_monday(self):
        return self.starts_on - timedelta(
            days=self.starts_on.weekday(), weeks=self.first_week - 1
        )

    def week_number(self, day):
        if not self.starts_on <= day <= self.ends_on:
            return None
        return (day - self.week_one_monday).days // 7 + 1

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError("学期结束日期不能早于开始日期。")
        if (
            self.starts_on
            and self.ends_on
            and (self.ends_on - self.week_one_monday).days >= 30 * 7
        ):
            raise ValidationError("学期末的周数不能超过第 30 周。")


class BorrowPeriod(models.Model):
    number = models.PositiveSmallIntegerField(
        "节次序号", unique=True, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    start = models.TimeField("实际开始时间")
    end = models.TimeField("实际结束时间")

    class Meta:
        ordering = ["number"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(number__gte=1, number__lte=5),
                name="five_borrow_periods",
            ),
            models.CheckConstraint(
                condition=models.Q(end__gt=models.F("start")),
                name="period_end_after_start",
            ),
        ]

    def __str__(self):
        return ["第一节", "第二节", "第三节", "第四节", "第五节"][self.number - 1]

    @property
    def group(self):
        return "上午" if self.number <= 2 else "下午" if self.number <= 4 else "晚上"
