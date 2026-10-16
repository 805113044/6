import csv
import hashlib
from datetime import date, datetime, time, timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.debug import sensitive_post_parameters
from .forms import (
    BookingForm,
    BookingEditForm,
    CourseImportForm,
    FurnitureLoanForm,
    StudentBookingForm,
)
from .models import Booking, FurnitureLoan, LoginAttempt, Room, TeachingRule
from .services import (
    accessible_rooms,
    managed_rooms,
    create_booking,
    decide,
    cancel_booking,
    import_course_rows,
    can_view_details,
    guest_owns,
    grant_guest,
    grant_guests,
    lookup_guest_bookings,
    verify_guest,
    edit_booking,
)


def throttle(request, scope, limit=20, minutes=15, *, identity=None):
    # Nginx must overwrite X-Real-IP; the app port is bound only to loopback.
    value = (
        identity
        if identity is not None
        else request.META.get(
            "HTTP_X_REAL_IP", request.META.get("REMOTE_ADDR", "unknown")
        )
    )
    key = hashlib.sha256(f"{scope}:{value}".encode()).hexdigest()
    with transaction.atomic():
        attempt, _ = LoginAttempt.objects.select_for_update().get_or_create(key=key)
        if attempt.updated_at < timezone.now() - timedelta(minutes=minutes):
            attempt.failures = 0
        if attempt.failures >= limit:
            return False
        attempt.failures += 1
        attempt.updated_at = timezone.now()
        attempt.save()
    return True


class SchoolLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        if not form.get_user().is_staff:
            form.add_error(None, "此入口仅供管理员使用；普通用户无需登录。")
            return self.form_invalid(form)
        return super().form_valid(form)

    def post(self, request, *args, **kwargs):
        if not throttle(
            request,
            "login-account",
            10,
            identity=request.POST.get("username", "").strip().casefold(),
        ) or not throttle(request, "login-ip", 300):
            return render(
                request,
                self.template_name,
                {"form": self.get_form(), "limited": True},
                status=429,
            )
        return super().post(request, *args, **kwargs)


def selected_day(request):
    from .models import AcademicTerm

    term = AcademicTerm.objects.filter(pk=1).first()
    week = request.GET.get("week", "")
    if term and week.isdigit() and term.first_week <= int(week) <= 30:
        return term.week_one_monday + timedelta(weeks=int(week) - 1)
    try:
        return date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return timezone.localdate()


def day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def room_filters(request, qs):
    for key in ("campus", "building", "room_type"):
        if request.GET.get(key):
            qs = qs.filter(**{key: request.GET[key]})
    for key in ("capacity", "floor"):
        value = request.GET.get(key, "")
        if value.isdigit() and len(value) < 5:
            qs = qs.filter(
                **{("capacity__gte" if key == "capacity" else key): int(value)}
            )
    if request.GET.get("q"):
        qs = qs.filter(
            Q(name__icontains=request.GET["q"][:60])
            | Q(building__icontains=request.GET["q"][:60])
            | Q(equipment__icontains=request.GET["q"][:60])
        )
    return qs


def rooms(request):
    from .models import AcademicTerm, BorrowPeriod
    from .school import availability

    day = selected_day(request)
    periods = list(BorrowPeriod.objects.all())
    base = accessible_rooms(request.user).filter(is_active=True)
    candidates = list(room_filters(request, base))
    matrix = availability(candidates, day, periods)
    selected = request.GET.get("period", "")
    if selected.isdigit():
        candidates = [
            r
            for r in candidates
            if any(c["period"].pk == int(selected) and c["free"] for c in matrix[r.pk])
        ]
    page = Paginator(candidates, 12).get_page(request.GET.get("page"))
    for room in page:
        room.cells = matrix[room.pk]
        room.daily_count = sum(bool(c["entries"]) for c in room.cells)
    params = request.GET.copy()
    params.pop("page", None)
    term = AcademicTerm.objects.filter(pk=1).first()
    return render(
        request,
        "booking/rooms.html",
        {
            "page": page,
            "query_string": params.urlencode(),
            "day": day,
            "periods": periods,
            "term": term,
            "week_number": term.week_number(day) if term else None,
            "campuses": base.values_list("campus", flat=True)
            .distinct()
            .order_by("campus"),
            "buildings": base.values_list("building", flat=True)
            .distinct()
            .order_by("building"),
            "room_types": Room._meta.get_field("room_type").choices,
            "room_count": base.count(),
            "today_count": sum(
                bool(c["entries"]) for row in matrix.values() for c in row
            ),
            "pending_count": Booking.objects.occupying()
            .filter(kind="booking", end_at__gt=timezone.now())
            .count()
            if request.user.is_staff
            else 0,
        },
    )


def schedule(request):
    from .models import AcademicTerm, BorrowPeriod
    from .school import availability

    day = selected_day(request)
    mode = "day" if request.GET.get("view") == "day" else "week"
    first = day if mode == "day" else day - timedelta(days=day.weekday())
    days = [first + timedelta(days=i) for i in range(1 if mode == "day" else 7)]
    periods = list(BorrowPeriod.objects.all())
    rooms = list(
        room_filters(request, accessible_rooms(request.user).filter(is_active=True))
    )
    matrices = {d: availability(rooms, d, periods) for d in days}
    rows = []
    for index, period in enumerate(periods):
        rows.append(
            {
                "period": period,
                "cells": [
                    {
                        "day": d,
                        "rooms": [
                            {"room": room, "slot": matrices[d][room.pk][index]}
                            for room in rooms
                        ],
                    }
                    for d in days
                ],
            }
        )
    params = request.GET.copy()
    params.pop("page", None)
    navigation = params.copy()
    navigation["date"] = (first - timedelta(days=len(days))).isoformat()
    previous = navigation.urlencode()
    navigation["date"] = (first + timedelta(days=len(days))).isoformat()
    following = navigation.urlencode()
    term = AcademicTerm.objects.filter(pk=1).first()
    return render(
        request,
        "booking/schedule.html",
        {
            "rows": rows,
            "columns": days,
            "room_count": len(rooms),
            "mode": mode,
            "day": day,
            "first": first,
            "last": days[-1],
            "previous": previous,
            "following": following,
            "query_string": params.urlencode(),
            "room_types": Room._meta.get_field("room_type").choices,
            "term": term,
            "week_number": (
                term.week_number(max(first, term.starts_on))
                if mode == "week"
                and term
                and first <= term.ends_on
                and days[-1] >= term.starts_on
                else term.week_number(day)
                if term
                else None
            ),
            "days": days,
        },
    )


@sensitive_post_parameters("phone", "student_id")
@require_http_methods(["GET", "HEAD", "POST"])
def furniture_loans(request):
    form = FurnitureLoanForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not throttle(request, "furniture-loan", 30):
            form.add_error(None, "登记过于频繁，请 15 分钟后再试。")
        else:
            data = form.cleaned_data
            loan = FurnitureLoan.objects.create(
                borrower_name=data["borrower_name"],
                phone=data["phone"],
                student_id=data["student_id"],
                purpose=data["purpose"],
                items=data["items"],
            )
            messages.success(request, f"桌椅借用登记成功，编号 {loan.pk}。")
            return redirect("furniture_loans")
    if request.method == "POST":
        item_rows = list(
            zip(request.POST.getlist("item_name"), request.POST.getlist("item_quantity"))
        )[:20]
    else:
        item_rows = [("", "")]
    if not item_rows:
        item_rows = [("", "")]
    page = None
    if request.user.is_staff:
        loans = FurnitureLoan.objects.filter(
            created_at__gt=timezone.now() - timedelta(days=settings.RETENTION_DAYS)
        )
        query = request.GET.get("q", "").strip()[:60]
        if query:
            loans = loans.filter(
                Q(borrower_name__icontains=query)
                | Q(student_id__icontains=query)
                | Q(phone__icontains=query)
            )
        state = request.GET.get("state", "")
        if state in ("open", "returned"):
            loans = loans.filter(returned_at__isnull=state == "open")
        page = Paginator(loans, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "booking/furniture_loans.html",
        {"form": form, "item_rows": item_rows, "page": page},
    )


@sensitive_post_parameters(
    "edit_password", "edit_password_confirm", "student_id", "student_contact"
)
def room_detail(request, pk):
    room = get_object_or_404(
        accessible_rooms(request.user).filter(is_active=True), pk=pk
    )
    day = selected_day(request)
    form_class = BookingForm if request.user.is_staff else StudentBookingForm
    form = form_class(
        request.POST or None,
        initial={"date": day, "participants": 1, "period": request.GET.get("period")},
    )
    if request.method == "POST" and form.is_valid():
        if not throttle(request, "booking", 100):
            form.add_error(None, "提交较频繁，请 15 分钟后重试。")
        else:
            data = form.cleaned_data
            try:
                booking, code = create_booking(
                    request.user,
                    room.pk,
                    title=data["title"],
                    start_at=data["start_at"],
                    end_at=data["end_at"],
                    participants=data["participants"],
                    student={
                        k: data[k]
                        for k in (
                            "student_name",
                            "student_id",
                            "student_department",
                            "student_contact",
                            "edit_password",
                        )
                    }
                    if not request.user.is_staff
                    else None,
                )
                if not request.user.is_staff:
                    grant_guest(request, booking)
                    request.session["guest_booking"] = booking.pk
                    return redirect("receipt")
                messages.success(
                    request,
                    "预约已生效。"
                    if booking.status == "approved"
                    else "预约已提交，等待管理员审批。",
                )
                return redirect("my_bookings")
            except ValidationError as exc:
                form.add_error(None, exc)
    from .models import BorrowPeriod, AcademicTerm
    from .school import availability

    periods = list(BorrowPeriod.objects.all())
    cells = availability([room], day, periods)[room.pk]
    term = AcademicTerm.objects.filter(pk=1).first()
    return render(
        request,
        "booking/detail.html",
        {
            "room": room,
            "day": day,
            "cells": cells,
            "form": form,
            "advance_days": settings.BOOKING_ADVANCE_DAYS,
            "term": term,
            "week_number": term.week_number(day) if term else None,
        },
    )


def receipt(request):
    booking = get_object_or_404(
        Booking.objects.retained(), pk=request.session.get("guest_booking")
    )
    if not guest_owns(request, booking):
        return redirect("verify_booking", pk=booking.pk)
    return render(
        request,
        "booking/receipt.html",
        {"booking": booking, "owner": True},
    )


@sensitive_post_parameters("password", "student_id")
def lookup(request):
    if request.method == "POST":
        if not throttle(request, "lookup", 300) or not throttle(
            request,
            "lookup-identity",
            15,
            identity=request.POST.get("student_id", "")[:40].strip(),
        ):
            messages.error(request, "查询频繁，请稍后再试。")
        else:
            pk = request.POST.get("booking_id", "")[:15]
            identity = request.POST.get("student_id", "")[:40].strip()
            password = request.POST.get("password", "")[:128]
            if pk:
                # Keep old saved forms compatible; the new interface omits ID.
                booking = (
                    Booking.objects.retained().filter(pk=int(pk)).first()
                    if pk.isascii() and pk.isdecimal()
                    else None
                )
                if verify_guest(booking, identity, password):
                    grant_guest(request, booking)
                    request.session["guest_booking"] = booking.pk
                    return redirect("receipt")
            else:
                matched = lookup_guest_bookings(identity, password)
                if matched:
                    grant_guests(request, matched)
                    return redirect("lookup")
            messages.error(
                request, "学号 / 工号或本次密码不正确，或没有尚在保留期内的匹配记录。"
            )
    grants = request.session.get("booking_grants", {})
    ids = [int(pk) for pk in grants if pk.isdigit()]
    bookings = [
        b
        for b in Booking.objects.retained()
        .filter(pk__in=ids)
        .select_related("room", "period")
        .order_by("-start_at")
        if guest_owns(request, b)
    ]
    return render(request, "booking/lookup.html", {"bookings": bookings})


@sensitive_post_parameters("password", "student_id")
def verify_booking(request, pk):
    booking = get_object_or_404(Booking.objects.retained(), pk=pk)
    if can_view_details(request.user) or guest_owns(request, booking):
        return redirect("booking_info", pk=pk)
    if request.method == "POST":
        if not throttle(request, "verify", 30) or not throttle(
            request, "verify-record", 15, identity=str(pk)
        ):
            messages.error(request, "验证次数过多，请 15 分钟后重试。")
        elif verify_guest(
            booking,
            request.POST.get("student_id", "")[:40],
            request.POST.get("password", "")[:128],
        ):
            grant_guest(request, booking)
            return redirect("booking_info", pk=pk)
        else:
            messages.error(request, "学号 / 工号或本次预约密码不正确。")
    return render(request, "booking/verify.html", {"booking": booking})


def booking_info(request, pk):
    booking = get_object_or_404(
        Booking.objects.retained().select_related("room", "applicant"), pk=pk
    )
    owner = guest_owns(request, booking)
    if not can_view_details(request.user) and not owner:
        return redirect("verify_booking", pk=pk)
    return render(
        request,
        "booking/receipt.html",
        {"booking": booking, "owner": owner, "admin": request.user.is_staff},
    )


@require_POST
def forget_guest(request):
    request.session.pop("booking_grant", None)
    request.session.pop("booking_grants", None)
    request.session.pop("guest_booking", None)
    return redirect("lookup")


@sensitive_post_parameters("edit_password", "student_id", "student_contact")
def booking_edit(request, pk):
    booking = get_object_or_404(
        Booking.objects.retained().select_related("room"), pk=pk
    )
    owner = guest_owns(request, booking)
    if not request.user.is_staff and not owner:
        return redirect("verify_booking", pk=pk)
    local_start = timezone.localtime(booking.start_at)
    from .school import period_for

    initial = {
        "room": booking.room_id,
        "date": local_start.date(),
        "period": booking.period_id
        or getattr(period_for(booking.start_at, booking.end_at), "pk", None),
        "title": booking.title,
        "participants": booking.participants,
    }
    for key in ("student_name", "student_id", "student_department", "student_contact"):
        initial[key] = getattr(booking, key)
    form = BookingEditForm(
        request.POST or None,
        initial=initial,
        rooms=accessible_rooms(request.user),
        admin=request.user.is_staff,
    )
    if booking.applicant_id is not None:
        for key in (
            "student_name",
            "student_id",
            "student_department",
            "student_contact",
            "edit_password",
        ):
            form.fields.pop(key, None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            updated = edit_booking(
                request.user,
                pk,
                room_id=data["room"].pk,
                title=data["title"],
                start_at=data["start_at"],
                end_at=data["end_at"],
                participants=data["participants"],
                student={
                    k: data.get(k, "")
                    for k in (
                        "student_name",
                        "student_id",
                        "student_department",
                        "student_contact",
                        "edit_password",
                    )
                }
                if booking.applicant_id is None
                else None,
                guest_authorized=owner,
                expected_version=booking.credential_version,
                reason=data.get("reason", ""),
            )
            if owner:
                grant_guest(request, updated)
            messages.success(request, "修改已保存，预约已生效。")
            return redirect("booking_info", pk=pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "booking/edit.html", {"form": form, "booking": booking})


@login_required
def my_bookings(request):
    qs = (
        Booking.objects.retained()
        .filter(applicant=request.user, kind="booking")
        .select_related("room")
    )
    status = request.GET.get("status", "")
    if status in Booking.Status.values:
        qs = qs.filter(status=status)
    return render(
        request,
        "booking/bookings.html",
        {
            "page": Paginator(qs, 20).get_page(request.GET.get("page")),
            "states": Booking.Status.choices,
        },
    )


@require_POST
def cancel(request, pk):
    booking = get_object_or_404(Booking.objects.retained(), pk=pk)
    guest_authorized = guest_owns(request, booking)
    try:
        cancel_booking(
            request.user,
            pk,
            request.POST.get("reason", ""),
            guest_authorized=guest_authorized,
            expected_version=booking.credential_version,
        )
        messages.success(request, "预约已取消，该时段已释放。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("booking_info", pk=pk)


staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)


@staff_required
@require_POST
def return_furniture_loan(request, pk):
    loan = get_object_or_404(
        FurnitureLoan.objects.filter(
            created_at__gt=timezone.now() - timedelta(days=settings.RETENTION_DAYS)
        ),
        pk=pk,
    )
    if loan.returned_at is None:
        loan.returned_at = timezone.now()
        loan.save(update_fields=["returned_at"])
        messages.success(request, f"登记 #{loan.pk} 已标记归还。")
    return redirect("furniture_loans")


@staff_required
def management(request):
    from .models import AcademicTerm, User

    term = AcademicTerm.objects.filter(pk=1).first()
    return render(
        request,
        "booking/management.html",
        {
            "room_count": Room.objects.count(),
            "booking_count": Booking.objects.occupying()
            .filter(kind="booking", end_at__gt=timezone.now())
            .count(),
            "admin_count": User.objects.filter(is_staff=True, is_active=True).count(),
            "term": term,
            "week_number": term.week_number(timezone.localdate()) if term else None,
        },
    )


@staff_required
def administrators(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    from .models import User

    return render(
        request,
        "booking/administrators.html",
        {"accounts": User.objects.filter(is_staff=True).order_by("username")},
    )


@staff_required
@sensitive_post_parameters("password1", "password2")
def administrator_edit(request, pk=None):
    if not request.user.is_superuser:
        raise PermissionDenied
    from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
    from django.contrib.contenttypes.models import ContentType
    from .forms import AdministratorCreateForm, AdministratorEditForm
    from .models import User

    with transaction.atomic():
        account = (
            get_object_or_404(User.objects.select_for_update(), pk=pk, is_staff=True)
            if pk
            else None
        )
        # Root accounts cannot be disabled or demoted in the daily manager UI.
        if account and account.is_superuser:
            raise PermissionDenied
        form = (
            AdministratorEditForm(request.POST or None, instance=account)
            if account
            else AdministratorCreateForm(request.POST or None)
        )
        if request.method == "POST" and form.is_valid():
            target = form.save(commit=False)
            target.is_staff = True
            target.is_superuser = False
            if account and form.cleaned_data.get("password1"):
                target.set_password(form.cleaned_data["password1"])
            target.save()
            LogEntry.objects.create(
                user_id=request.user.pk,
                content_type=ContentType.objects.get_for_model(User),
                object_id=target.pk,
                object_repr=str(target),
                action_flag=CHANGE if account else ADDITION,
                change_message="更新管理员资料、启用状态或密码"
                if account
                else "创建管理员",
            )
            messages.success(
                request, "管理员账号已保存；每位管理员使用自己的账号登录。"
            )
            return redirect("administrators")
    return render(
        request, "booking/administrator_edit.html", {"form": form, "account": account}
    )


@staff_required
def review_queue(request):
    qs = (
        Booking.objects.retained()
        .filter(room__in=managed_rooms(request.user))
        .select_related("room", "applicant")
    )
    status = request.GET.get("status", "approved")
    if status in Booking.Status.values:
        qs = qs.filter(status=status)
    return render(
        request,
        "booking/review.html",
        {
            "page": Paginator(qs, 25).get_page(request.GET.get("page")),
            "states": Booking.Status.choices,
            "active_status": status,
        },
    )


@staff_required
@require_POST
def review_booking(request, pk):
    get_object_or_404(
        Booking.objects.retained().filter(room__in=managed_rooms(request.user)), pk=pk
    )
    try:
        decide(
            request.user,
            pk,
            request.POST.get("decision"),
            request.POST.get("reason", ""),
        )
        messages.success(request, "审批结果已保存。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("review")


@staff_required
def import_courses(request):
    form = CourseImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        try:
            rows = form.cleaned_data["file"]
            new_rooms = len(
                {r["room"] for r in rows}
                - set(
                    Room.objects.filter(name__in=[r["room"] for r in rows]).values_list(
                        "name", flat=True
                    )
                )
            )
            created, skipped = import_course_rows(
                request.user,
                form.cleaned_data["file"],
                dry_run=not form.cleaned_data["confirm"],
            )
            result = {
                "created": created,
                "skipped": skipped,
                "new_rooms": new_rooms,
                "saved": form.cleaned_data["confirm"],
            }
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(
        request,
        "booking/import.html",
        {
            "form": form,
            "result": result,
            "rules": TeachingRule.objects.select_related("room").order_by("-id")[:100],
        },
    )


@staff_required
@require_POST
def stop_timetable(request, pk):
    from .timetable import stop_rule

    get_object_or_404(TeachingRule, pk=pk)
    try:
        stop_rule(request.user, pk, request.POST.get("reason", ""))
        messages.success(request, "规则已停用，未来占用已取消。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect("import_courses")


@staff_required
def course_template(request):
    recurring = request.GET.get("kind") != "date"
    header = (
        [
            "教室",
            "课程名称",
            "任课教师",
            "周次",
            "单双周",
            "星期",
            "节次",
            "排除日期",
            "校区",
            "楼栋",
            "楼层",
            "教室类型",
            "容量",
        ]
        if recurring
        else [
            "教室",
            "课程名称",
            "任课教师",
            "日期",
            "节次",
            "校区",
            "楼栋",
            "楼层",
            "教室类型",
            "容量",
        ]
    )
    sample = (
        [
            "A101",
            "高等数学",
            "张老师",
            "1-18",
            "单周",
            "星期三",
            "第一节",
            "",
            "主校区",
            "教学楼",
            "1",
            "教室",
            "60",
        ]
        if recurring
        else [
            "A101",
            "高等数学",
            "张老师",
            (timezone.localdate() + timedelta(days=2)).isoformat(),
            "第一节",
            "主校区",
            "教学楼",
            "1",
            "教室",
            "60",
        ]
    )
    # Numeric section values are equally accepted; Chinese labels are clearer for schools.
    sample[6 if recurring else 4] = "1"
    if request.GET.get("format") != "csv":
        from openpyxl import Workbook

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="class-timetable.xlsx"'
        wb = Workbook()
        sheet = wb.active
        sheet.title = "正式课程"
        sheet.append(header)
        sheet.append(sample)
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 18
        wb.save(response)
        return response
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="class-timetable.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerow(sample)
    return response


@staff_required
def school_settings(request):
    from .forms import SchoolSettingsForm
    from .models import AcademicTerm, BorrowPeriod
    from .school import save_school

    term = AcademicTerm.objects.filter(pk=1).first()
    initial = (
        {
            field: getattr(term, field)
            for field in ("name", "starts_on", "ends_on", "first_week", "booking_open")
        }
        if term
        else {}
    )
    for p in BorrowPeriod.objects.all():
        initial[f"p{p.number}_start"] = p.start
        initial[f"p{p.number}_end"] = p.end
    form = SchoolSettingsForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            save_school(request.user, form.cleaned_data)
            messages.success(request, "学期与五个借用节次已保存。")
            return redirect("school_settings")
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "booking/school_settings.html", {"form": form, "term": term})


@staff_required
def manage_rooms(request):
    return render(request, "booking/manage_rooms.html", {"rooms": Room.objects.all()})


@staff_required
def room_edit(request, pk=None):
    from .forms import RoomForm

    room = get_object_or_404(Room, pk=pk) if pk else None
    form = RoomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "教室资料已保存。")
        return redirect("manage_rooms")
    return render(request, "booking/room_edit.html", {"form": form, "room": room})


def safe_csv(value):
    # Prevent spreadsheet formula injection, including whitespace-prefixed formulas.
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


@staff_required
def reports(request):
    qs = (
        Booking.objects.retained()
        .filter(room__in=managed_rooms(request.user), kind="booking")
        .select_related("room", "applicant")
    )
    if request.GET.get("room", "").isdigit():
        qs = qs.filter(room_id=request.GET["room"])
    for param in ("from", "to"):
        try:
            start, end = day_bounds(date.fromisoformat(request.GET.get(param, "")))
            # UTC boundaries also work when managed MySQL lacks timezone tables.
            qs = qs.filter(
                **(
                    {"start_at__gte": start}
                    if param == "from"
                    else {"start_at__lt": end}
                )
            )
        except ValueError:
            pass
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="classroom-statistics.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response)
        # Export aggregated data only; no student names, IDs or contact details.
        writer.writerow(["教室", "状态", "预约数量", "总时长（小时）"])
        groups = {}
        for b in qs:
            key = (b.room.name, b.get_status_display())
            count, hours = groups.get(key, (0, 0))
            groups[key] = (
                count + 1,
                hours + (b.end_at - b.start_at).total_seconds() / 3600,
            )
        for (name, status), (count, hours) in sorted(groups.items()):
            writer.writerow([safe_csv(name), status, count, round(hours, 2)])
        return response
    counts = dict(qs.values_list("status").annotate(n=Count("id")))
    return render(
        request,
        "booking/reports.html",
        {
            "counts": counts,
            "total": qs.count(),
            "rooms": managed_rooms(request.user),
            "summary": qs.values("room__name")
            .annotate(n=Count("id"))
            .order_by("-n")[:20],
        },
    )


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
