"""V3 furniture stock/returns and timetable quick-book endpoints.

The public registration form prominently states that new entries display complete
student IDs without a separate checkbox. Historic non-public rows remain private. This module is installed with a repeatable
one-file, offline Docker upgrade; it reuses V2 models and booking services.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import secrets

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .forms import BookingForm, StudentBookingForm, FurnitureLoanForm
from .models import AuditEvent, Booking, BorrowPeriod, FurnitureLoan, FurnitureStock, Room
from .services import create_booking, grant_guest, managed_rooms, accessible_rooms
from .views import throttle

_DUMMY_RETURN_HASH = make_password(secrets.token_urlsafe(18))


staff_only = user_passes_test(lambda u: u.is_authenticated and u.is_active and u.is_staff)


class StockEntryForm(forms.Form):
    name = forms.CharField(max_length=60)
    total_count = forms.IntegerField(min_value=0, max_value=100000)


def _active_loans():
    cutoff = timezone.now() - timedelta(days=settings.FURNITURE_RETENTION_DAYS)
    return FurnitureLoan.objects.filter(
        Q(returned_at__isnull=True) | Q(returned_at__gt=cutoff)
    )


def _stock_and_balance():
    return list(FurnitureStock.objects.filter(is_active=True).order_by('name'))


def _loan_rows(request):
    # Only explicitly public legacy rows or new notice-disclosed entries are shown; no phone.
    return Paginator(_active_loans().filter(public_consent=True).select_related('registered_by', 'returned_by').order_by('-created_at'), 30).get_page(
        request.GET.get('page')
    )


@transaction.atomic
def _make_loan(form, user=None):
    """Lock stock rows before validating availability and writing the loan."""
    entries = form.cleaned_data['items']
    # "其他" is explicitly untracked, never changes an administrator's inventory.
    managed = [item for item in entries if not item.get('untracked')]
    names = [item['name'] for item in managed]
    stocks = list(FurnitureStock.objects.select_for_update().filter(
        name__in=names, is_active=True
    ).order_by('pk'))
    by_name = {stock.name: stock for stock in stocks}
    if len(by_name) != len(set(names)):
        raise ValidationError('物品名称不在当前可借库存，请刷新页面重新选择。')
    for item in managed:
        stock = by_name[item['name']]
        if stock.total_count - stock.loaned_count < item['quantity']:
            raise ValidationError(f'{stock.name} 余量不足，目前可借 {max(0,stock.total_count-stock.loaned_count)} 件。')
    for item in managed:
        stock = by_name[item['name']]
        stock.loaned_count += item['quantity']
        stock.save(update_fields=['loaned_count'])
    is_staff = bool(user and user.is_authenticated and user.is_staff)
    operator_name = ((getattr(user, 'display_name', '') or user.get_full_name() or user.username).strip()
                     if is_staff else '')
    borrower_name = (form.cleaned_data.get('borrower_name') or '').strip() or operator_name
    if not borrower_name:
        raise ValidationError('请填写借用人姓名。')
    loan = FurnitureLoan.objects.create(
        borrower_name=borrower_name,
        registered_by=user if is_staff else None,
        student_id=form.cleaned_data['student_id'],
        phone=form.cleaned_data['phone'],
        purpose=form.cleaned_data['purpose'],
        items=entries,
        return_code_hash=make_password(form.cleaned_data['loan_password']),
        public_consent=True,
        public_student_id=True,
    )
    return loan, None


def _return_locked(loan, *, user=None):
    """Called with atomic transaction; serializes changes on stock and loan."""
    if loan.returned_at is not None:
        return False
    names = {item.get('name') for item in loan.items if not item.get('untracked')}
    stocks = list(FurnitureStock.objects.select_for_update().filter(
        name__in=names
    ).order_by('pk'))
    by_name = {stock.name: stock for stock in stocks}
    for item in loan.items:
        if item.get('untracked'):
            continue
        stock = by_name.get(item['name'])
        if stock:
            stock.loaned_count = max(0, stock.loaned_count - int(item['quantity']))
            stock.save(update_fields=['loaned_count'])
    loan.returned_at = timezone.now()
    loan.returned_by = user if user and user.is_authenticated else None
    loan.return_source = 'admin' if user else 'student'
    loan.save(update_fields=['returned_at', 'returned_by', 'return_source'])
    return True


@sensitive_post_parameters('student_id', 'phone', 'loan_password', 'loan_password_confirm')
def furniture(request):
    """Public dashboard/table, borrower registration, and staff-only management."""
    is_staff = bool(request.user.is_authenticated and request.user.is_staff)
    operator_name = ((getattr(request.user, 'display_name', '') or request.user.get_full_name() or request.user.username).strip()
                     if is_staff else '')
    form = FurnitureLoanForm(request.POST or None,
                             initial={'borrower_name': operator_name} if is_staff and request.method != 'POST' else None)
    # Staff can leave borrower_name empty: account's display name is used.
    if is_staff:
        form.fields['borrower_name'].required = False
    issued = None
    if request.method == 'POST':
        if form.is_valid():
            if not throttle(request, 'furniture-loan', 30):
                form.add_error(None, '提交过于频繁，请稍后重试。')
            else:
                try:
                    issued = _make_loan(form, request.user)
                    messages.success(request, f'桌椅借用登记成功，编号 #{issued[0].pk}。刷新页面不会再次借用。')
                    return redirect('furniture_loans')
                except ValidationError as exc:
                    form.add_error(None, exc)
        else:
            pass
    if request.method == 'POST':
        old_names = request.POST.getlist('item_name')[:20]
        old_quantities = request.POST.getlist('item_quantity')[:20]
        old_customs = request.POST.getlist('item_custom_name')[:20]
        old_rows = [(n, old_quantities[i] if i < len(old_quantities) else '',
                     old_customs[i] if i < len(old_customs) else '')
                    for i, n in enumerate(old_names)]
    else:
        old_rows = []
    if not old_rows:
        old_rows = [('', '', '')]
    staff_page = None
    if request.user.is_authenticated and request.user.is_staff:
        qs = _active_loans()
        q = request.GET.get('q', '').strip()[:60]
        if q:
            qs = qs.filter(Q(borrower_name__icontains=q) |
                           Q(student_id__icontains=q) | Q(phone__icontains=q))
        state = request.GET.get('state')
        if state == 'open':
            qs = qs.filter(returned_at__isnull=True)
        elif state == 'returned':
            qs = qs.filter(returned_at__isnull=False)
        staff_page = Paginator(qs, 30).get_page(request.GET.get('staff_page'))
    context = {
        'form': form,
        'item_rows': old_rows,
        'inventory': _stock_and_balance(),
        'public_page': _loan_rows(request),
        'staff_page': staff_page,
        'issued_loan': issued[0] if issued else None,
    }
    response = render(request, 'booking/furniture_v3.html', context)
    response['Cache-Control'] = 'no-store, private'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@staff_only
@require_POST
def stock_save(request):
    form = StockEntryForm(request.POST)
    if not form.is_valid():
        messages.error(request, '库存资料无效：请填写物品名称和非负整数总数。')
        return redirect('furniture_loans')
    name, total = form.cleaned_data['name'].strip(), form.cleaned_data['total_count']
    with transaction.atomic():
        stock = FurnitureStock.objects.select_for_update().filter(name=name).first()
        if stock is None:
            # Creating inventory makes no assumptions about past untracked loans.
            stock = FurnitureStock.objects.create(name=name, total_count=total)
        else:
            if total < stock.loaned_count:
                messages.error(request, f'{name} 未归还 {stock.loaned_count} 件，库存总数不能小于该数字。')
                return redirect('furniture_loans')
            stock.total_count = total
            stock.is_active = True
            stock.save(update_fields=['total_count', 'is_active'])
    messages.success(request, f'{name} 库存总数已更新，当前可借数量自动计算。')
    return redirect('furniture_loans')



# R7 stock name/total editing and safe removal. Booking, returns and stock accounting unchanged.
def _has_unreturned_name(name):
    """Legacy JSON loans have no stock FK. Never orphan a still-outstanding item."""
    for items in FurnitureLoan.objects.filter(returned_at__isnull=True).values_list('items', flat=True).iterator(chunk_size=200):
        if any(str(item.get('name', '')).strip() == name for item in (items or []) if isinstance(item, dict)):
            return True
    return False


def _stock_admin_log(user, stock, message, *, deletion=False):
    from django.contrib.admin.models import LogEntry, CHANGE, DELETION
    from django.contrib.contenttypes.models import ContentType
    LogEntry.objects.log_action(
        user_id=user.pk,
        content_type_id=ContentType.objects.get_for_model(FurnitureStock).pk,
        object_id=stock.pk,
        object_repr=stock.name,
        action_flag=DELETION if deletion else CHANGE,
        change_message=message,
    )


@staff_only
@require_POST
def stock_update(request, pk):
    form = StockEntryForm(request.POST)
    if not form.is_valid():
        messages.error(request, '物品名称应为 1–60 字；库存总数须为 0–100000 的整数。')
        return redirect('furniture_loans')
    new_name = form.cleaned_data['name'].strip()
    total = form.cleaned_data['total_count']
    with transaction.atomic():
        stock = get_object_or_404(FurnitureStock.objects.select_for_update(), pk=pk, is_active=True)
        if total < stock.loaned_count:
            messages.error(request, f'{stock.name} 尚有 {stock.loaned_count} 件未归还，总数不能小于未归还数量。')
            return redirect('furniture_loans')
        if new_name != stock.name:
            if stock.loaned_count or _has_unreturned_name(stock.name):
                messages.error(request, f'{stock.name} 仍有未归还记录；可修改总件数，待全部归还后再改名称。')
                return redirect('furniture_loans')
            if FurnitureStock.objects.exclude(pk=stock.pk).filter(name=new_name).exists():
                messages.error(request, f'物品名称“{new_name}”已存在，请使用其他名称。')
                return redirect('furniture_loans')
        old_name, old_total = stock.name, stock.total_count
        stock.name = new_name
        stock.total_count = total
        # Even a name-only change preserves outstanding accounting quantities.
        stock.save(update_fields=['name', 'total_count'])
        _stock_admin_log(request.user, stock,
            f'库存修改：{old_name} → {new_name}，总数 {old_total} → {total}（借出数未变）')
    messages.success(request, f'{new_name} 已保存：库存总数 {total}，可借数量自动计算。')
    return redirect('furniture_loans')


@staff_only
@require_POST
def stock_delete(request, pk):
    """Soft-delete only: do not erase history or break JSON-based loan return."""
    with transaction.atomic():
        stock = get_object_or_404(FurnitureStock.objects.select_for_update(), pk=pk, is_active=True)
        if stock.loaned_count or _has_unreturned_name(stock.name):
            messages.error(request, f'{stock.name} 仍有未归还物品；请先处理归还，暂不能删除。')
            return redirect('furniture_loans')
        stock.is_active = False
        stock.save(update_fields=['is_active'])
        _stock_admin_log(request.user, stock,
            '管理员删除库存物品（逻辑停用）：保留已有归还历史，可通过原名称重新新增启用。',
            deletion=True)
        name = stock.name
    messages.success(request, f'{name} 已从库存表和借用选项移除，历史登记保留。')
    return redirect('furniture_loans')

@staff_only
@require_POST
def stock_reconcile(request):
    """Explicit physical count correction, not silently inferred from expired PII."""
    name = request.POST.get('name', '').strip()
    available_text = request.POST.get('available', '').strip()
    reason = request.POST.get('reason', '').strip()
    if not available_text.isdecimal() or not (5 <= len(reason) <= 200):
        messages.error(request, '盘点须填写当前实物可借数量及至少 5 个字的原因。')
        return redirect('furniture_loans')
    with transaction.atomic():
        stock = get_object_or_404(FurnitureStock.objects.select_for_update(), name=name)
        available = int(available_text)
        if available > stock.total_count:
            messages.error(request, '可借数量不能超过总库存。')
            return redirect('furniture_loans')
        from django.contrib.admin.models import LogEntry, CHANGE
        from django.contrib.contenttypes.models import ContentType
        before = stock.loaned_count
        stock.loaned_count = stock.total_count - available
        stock.save(update_fields=['loaned_count'])
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(FurnitureStock).pk,
            object_id=stock.pk,
            object_repr=stock.name,
            action_flag=CHANGE,
            change_message=f'库存盘点：借出记账 {before} → {stock.loaned_count}；说明：{reason}',
        )
    messages.success(request, f'{stock.name} 盘点数量已调整并记录操作日志。')
    return redirect('furniture_loans')


@sensitive_post_parameters('student_id', 'password')
@require_POST
def furniture_self_return(request):
    """Return all outstanding loans that match exactly student ID + chosen password.

    No public loan number required. Old system-generated token hashes still work
    only if the previous borrower retained that token; otherwise staff may return.
    """
    sid = request.POST.get('student_id', '').strip()
    password = request.POST.get('password', '')
    if not (1 <= len(sid) <= 40 and 1 <= len(password) <= 4096):
        messages.error(request, '请完整填写学号和借用时设置的密码。')
        return redirect('furniture_loans')
    if not throttle(request, 'furniture-return-ip', 10) or not throttle(
        request, 'furniture-return-id', 3, identity=sid
    ):
        messages.error(request, '尝试次数过多，请稍后再试。')
        return redirect('furniture_loans')
    returned = 0
    with transaction.atomic():
        # Lock all matching student's active rows to avoid duplicate stock credits.
        loans = list(_active_loans().filter(student_id=sid, returned_at__isnull=True)
                     .select_for_update().order_by('pk')[:201])
        if len(loans) > 200:
            messages.error(request, '此学号未归还登记较多，请联系管理员处理。')
            return redirect('furniture_loans')
        matched = []
        # Passwords are PBKDF2 hashes; do not log or compare raw text.
        for loan in loans:
            if loan.return_code_hash and check_password(password, loan.return_code_hash):
                matched.append(loan)
        if not matched:
            # Avoid revealing whether a student ID exists.
            check_password(password, _DUMMY_RETURN_HASH)
            messages.error(request, '学号与密码不匹配，或该密码下没有未归还的借用。')
            return redirect('furniture_loans')
        for loan in matched:
            if _return_locked(loan):
                returned += 1
    messages.success(request, f'已归还 {returned} 笔匹配的桌椅借用；对应库存已更新。')
    return redirect('furniture_loans')


@staff_only
@require_POST
def furniture_admin_return(request, pk):
    with transaction.atomic():
        loan = get_object_or_404(_active_loans().select_for_update(), pk=pk)
        if _return_locked(loan, user=request.user):
            messages.success(request, f'登记 #{pk} 已由管理员直接标记归还。')
        else:
            messages.info(request, f'登记 #{pk} 此前已经归还。')
    return redirect('furniture_loans')


@staff_only
@require_POST
def furniture_admin_delete(request, pk):
    """Admin-only removal with inventory-safe reversal for outstanding loans.

    The operator must explicitly confirm that the actual items were recovered
    or the original registration was void. Stock remains in sync with history.
    """
    from django.contrib.admin.models import LogEntry, DELETION
    from django.contrib.contenttypes.models import ContentType
    with transaction.atomic():
        loan = get_object_or_404(_active_loans().select_for_update(), pk=pk)
        outstanding = loan.returned_at is None
        if outstanding and request.POST.get('confirm_release') != 'yes':
            messages.error(request, '借用中的记录只有确认实物已收回或登记作废后才能删除。')
            return redirect('furniture_loans')
        if outstanding:
            _return_locked(loan, user=request.user)
        LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=ContentType.objects.get_for_model(FurnitureLoan).pk,
            object_id=loan.pk,
            object_repr=f'桌椅登记 #{loan.pk}',
            action_flag=DELETION,
            change_message='管理员删除桌椅登记' + ('并释放未归还库存' if outstanding else '（已归还）'),
        )
        loan.delete()
    messages.success(request, f'登记 #{pk} 已删除。' + (' 库存已同步释放。' if outstanding else ''))
    return redirect('furniture_loans')


@sensitive_post_parameters('student_id', 'student_contact', 'edit_password', 'edit_password_confirm')
@require_POST
def quick_book(request):
    """One-step booking with visible inline errors (AJAX) and normal POST fallback."""
    form = BookingForm(request.POST) if request.user.is_staff else StudentBookingForm(request.POST)
    ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        room_id = int(request.POST.get('room_id', ''))
    except (ValueError, TypeError):
        room_id = 0
    day_value = request.POST.get('date', '')
    try:
        datetime.strptime(day_value, '%Y-%m-%d')
        target = '/schedule/?view=day&date=' + day_value
    except (ValueError, TypeError):
        target = '/schedule/'

    def fail(detail, *, status=400):
        detail = str(detail)[:600]
        if ajax:
            return JsonResponse({'ok': False, 'message': detail}, status=status)
        messages.error(request, detail)
        return redirect(target)

    if not form.is_valid():
        errors = []
        for field, values in form.errors.items():
            label = form.fields[field].label if field in form.fields else '表单'
            errors.extend(f'{label}：{str(value)}' for value in values)
        return fail('请检查填写内容：' + '；'.join(errors))
    if not accessible_rooms(request.user).filter(pk=room_id, is_active=True).exists():
        return fail('请选择当前开放且可借用的教室。')
    if not throttle(request, 'booking', 100):
        return fail('提交过于频繁，请稍后重试。', status=429)
    data = form.cleaned_data
    try:
        booking, _ = create_booking(
            request.user, room_id,
            title=data['title'], start_at=data['start_at'], end_at=data['end_at'],
            participants=data['participants'],
            student={key: data[key] for key in (
                'student_name', 'student_id', 'student_department',
                'student_contact', 'edit_password'
            )} if not request.user.is_staff else None,
        )
    except ValidationError as exc:
        return fail('预约未生效：' + '；'.join(exc.messages), status=409)

    if not request.user.is_staff:
        grant_guest(request, booking)
        request.session['guest_booking'] = booking.pk
        next_url = '/receipt/'
    else:
        next_url = '/bookings/'
        messages.success(request, f'{booking.room.name} 已预约成功。')
    if ajax:
        return JsonResponse({'ok': True, 'message': '预约已成功，即将打开凭证页面。',
                             'redirect_url': next_url, 'booking_id': booking.pk})
    return redirect('receipt' if not request.user.is_staff else 'my_bookings')


@staff_only
@require_POST
def classroom_admin_return(request, pk):
    from .models import AcademicTerm
    with transaction.atomic():
        # Lock in the same order as room scheduling writes (term, room, booking).
        AcademicTerm.objects.select_for_update().filter(pk=1).first()
        first = get_object_or_404(Booking.objects.retained(), pk=pk)
        Room.objects.select_for_update().get(pk=first.room_id)
        booking = get_object_or_404(Booking.objects.select_for_update(), pk=pk)
        if booking.room_id != first.room_id:
            messages.error(request, '教室刚被更改，请刷新后重试。')
            return redirect('review')
        if booking.kind != Booking.Kind.BOOKING or booking.status not in (
            Booking.Status.APPROVED, Booking.Status.PENDING
        ):
            messages.error(request, '只有未归还的教室借用可以标记归还，课程不能归还。')
            return redirect('review')
        if not managed_rooms(request.user).filter(pk=booking.room_id).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        booking.status = Booking.Status.COMPLETED
        booking.reason = '管理员确认教室已归还'
        booking.reviewed_by = request.user
        booking.reviewed_at = timezone.now()
        booking.save(update_fields=['status','reason','reviewed_by','reviewed_at'])
        AuditEvent.objects.create(
            booking=booking, actor=request.user,
            action='管理员直接标记归还', note='无需学生填写身份信息'
        )
    messages.success(request, f'教室借用 #{pk} 已由管理员标记归还。')
    return redirect('review')

@staff_only
@require_POST
def furniture_operator_name(request):
    name = request.POST.get('display_name', '').strip()
    if not (1 <= len(name) <= 60):
        messages.error(request, '管理员显示名称应为 1 到 60 个字符。')
    else:
        request.user.display_name = name
        request.user.save(update_fields=['display_name'])
        messages.success(request, '管理员显示名称已保存，之后的借用登记将显示该名称。')
    return redirect('furniture_loans')
