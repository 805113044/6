import csv
import hashlib
from datetime import date, datetime, time, timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import BookingForm, CourseImportForm, StudentBookingForm
from .models import Booking, LoginAttempt, Room
from .services import (
    accessible_rooms,
    managed_rooms,
    create_booking,
    decide,
    cancel_booking,
    import_course_rows,
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
    day = selected_day(request)
    start, end = day_bounds(day)
    base = accessible_rooms(request.user).filter(is_active=True)
    qs = room_filters(request, base)
    occupied = Booking.objects.occupying().filter(start_at__lt=end, end_at__gt=start)
    # Optional availability filtering requires a full interval; any overlap blocks it.
    if request.GET.get("start") and request.GET.get("end"):
        try:
            a = timezone.make_aware(
                datetime.combine(day, time.fromisoformat(request.GET["start"]))
            )
            b = timezone.make_aware(
                datetime.combine(day, time.fromisoformat(request.GET["end"]))
            )
            if b <= a:
                raise ValueError
            qs = qs.exclude(
                pk__in=Booking.objects.occupying()
                .filter(start_at__lt=b, end_at__gt=a)
                .values("room_id")
            )
        except ValueError:
            messages.error(request, "空闲筛选的结束时间须晚于开始时间。")
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    # Materialize this page: MySQL rejects LIMIT inside an IN subquery.
    room_ids = [room.pk for room in page.object_list]
    counts = {
        r["room_id"]: r["n"]
        for r in occupied.filter(room_id__in=room_ids)
        .values("room_id")
        .annotate(n=Count("id"))
    }
    for room in page:
        room.daily_count = counts.get(room.pk, 0)
    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "booking/rooms.html",
        {
            "page": page,
            "query_string": params.urlencode(),
            "day": day,
            "campuses": base.values_list("campus", flat=True)
            .distinct()
            .order_by("campus"),
            "buildings": base.values_list("building", flat=True)
            .distinct()
            .order_by("building"),
            "room_types": Room._meta.get_field("room_type").choices,
            "room_count": base.count(),
            "today_count": occupied.filter(room__in=base).count(),
            "pending_count": Booking.objects.retained()
            .filter(room__in=managed_rooms(request.user), status="pending")
            .count(),
        },
    )


def room_detail(request, pk):
    room = get_object_or_404(
        accessible_rooms(request.user).filter(is_active=True), pk=pk
    )
    day = selected_day(request)
    form_class = BookingForm if request.user.is_authenticated else StudentBookingForm
    form = form_class(request.POST or None, initial={"date": day, "participants": 1})
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
                        )
                    }
                    if not request.user.is_authenticated
                    else None,
                )
                if code:
                    request.session["guest_booking"] = booking.pk
                    # Short-lived, displayed once; the database stores only its hash.
                    request.session["receipt_code"] = code
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
    start, end = day_bounds(day)
    slots = (
        Booking.objects.occupying()
        .filter(room=room, start_at__lt=end, end_at__gt=start)
        .order_by("start_at")
    )
    # Never expose applicant names, purpose or contact details in the public schedule.
    return render(
        request,
        "booking/detail.html",
        {
            "room": room,
            "day": day,
            "slots": slots,
            "form": form,
            "max_hours": settings.BOOKING_MAX_HOURS,
            "advance_days": settings.BOOKING_ADVANCE_DAYS,
            "cancel_minutes": settings.CANCEL_BEFORE_MINUTES,
        },
    )


def receipt(request):
    booking = get_object_or_404(
        Booking.objects.retained(), pk=request.session.get("guest_booking")
    )
    return render(
        request,
        "booking/receipt.html",
        {"booking": booking, "code": request.session.pop("receipt_code", "")},
    )


def lookup(request):
    if request.method == "POST":
        if not throttle(request, "lookup", 30):
            messages.error(request, "查询频繁，请稍后再试。")
        else:
            code = request.POST.get("code", "").strip()[:100]
            booking = (
                Booking.objects.retained()
                .filter(lookup_hash=hashlib.sha256(code.encode()).hexdigest())
                .first()
            )
            if booking:
                request.session["guest_booking"] = booking.pk
                return redirect("receipt")
            messages.error(request, "查询凭证无效或记录已过期。")
    return render(request, "booking/lookup.html")


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
    guest_authorized = (
        request.session.get("guest_booking") == pk and booking.applicant_id is None
    )
    try:
        cancel_booking(
            request.user,
            pk,
            request.POST.get("reason", ""),
            guest_authorized=guest_authorized,
        )
        messages.success(request, "预约已取消，该时段已释放。")
    except ValidationError as exc:
        messages.error(request, "；".join(exc.messages))
    return redirect(
        "receipt"
        if guest_authorized
        else "review"
        if request.user.is_staff
        else "my_bookings"
    )


staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)


@staff_required
def review_queue(request):
    qs = (
        Booking.objects.retained()
        .filter(room__in=managed_rooms(request.user))
        .select_related("room", "applicant")
    )
    status = request.GET.get("status", "pending")
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
            created, skipped = import_course_rows(
                request.user,
                form.cleaned_data["file"],
                dry_run=not form.cleaned_data["confirm"],
            )
            result = {
                "created": created,
                "skipped": skipped,
                "saved": form.cleaned_data["confirm"],
            }
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "booking/import.html", {"form": form, "result": result})


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
