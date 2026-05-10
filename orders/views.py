from datetime import datetime, time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import require_department
from .departments import DEPARTMENTS, VALID_SLUGS
from .forms import (
    OrderForm,
    OrderItemFormSet,
    ShirtVariantFormSet,
    COLLAR_SUGGESTIONS,
    SLEEVE_SUGGESTIONS,
)
from .models import Order, StageLog, Tailor
from .qr_utils import generate_qr_svg

DEPT_COOKIE_NAME = 'production_dept'
DEPT_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


def _is_admin(user):
    return user.is_superuser or user.groups.filter(name='admin').exists()


@login_required
def order_list(request):
    orders = Order.objects.prefetch_related('items').all()

    # Filter by status
    status = request.GET.get('status')
    if status:
        orders = orders.filter(status=status)

    # Search
    q = request.GET.get('q')
    if q:
        orders = orders.filter(
            Q(customer_name__icontains=q) |
            Q(shirt_name__icontains=q) |
            Q(order_number__icontains=q)
        )

    return render(request, 'orders/order_list.html', {
        'orders': orders,
        'current_status': status,
        'search_query': q or '',
        'status_choices': Order.STATUS_CHOICES,
    })


def _copy_images_from_first(request, formset):
    """Copy design_image from first saved item to items that checked 'copy image'."""
    saved = [f.instance for f in formset.forms if f.instance.pk and not f.cleaned_data.get('DELETE')]
    if len(saved) < 2:
        return
    first_img = saved[0].design_image
    if not first_img:
        return
    for i, item in enumerate(saved[1:], 1):
        cb_key = f'copy_image_{i}'
        if request.POST.get(cb_key) == 'on' and not item.design_image:
            item.design_image = first_img
            item.save()


# ---------------------------------------------------------------------------
# Nested formset helpers (Phase 1.7): Order → OrderItem → ShirtVariant
#
# Each outer item form gets a parallel ShirtVariantFormSet whose prefix is
# `items-{i}-variants`. The view validates all three levels (Order, items,
# variants), then saves outer-then-inner so the FK chain is intact.
# ---------------------------------------------------------------------------


def _variant_prefix(i):
    return f'items-{i}-variants'


def _build_variant_formsets(item_formset, post=None, files=None):
    """Return one ShirtVariantFormSet per outer item form, in order."""
    formsets = []
    for i, item_form in enumerate(item_formset.forms):
        kwargs = {
            'instance': item_form.instance,
            'prefix': _variant_prefix(i),
        }
        if post is not None:
            formsets.append(ShirtVariantFormSet(post, files, **kwargs))
        else:
            formsets.append(ShirtVariantFormSet(**kwargs))
    return formsets


def _empty_variant_formset(post=None, files=None):
    """Empty inner formset whose prefix uses literal '__prefix__' for the item index.

    Rendered via management_form + empty_form so JS can clone it when the user
    presses '+ เพิ่มรายการ' (a new item starts with one empty variant).
    """
    return ShirtVariantFormSet(prefix='items-__prefix__-variants')


def _variant_has_real_content(vform):
    """A variant counts as 'real' if any text field is filled OR sizes total > 0."""
    cd = getattr(vform, 'cleaned_data', None) or {}
    if any((cd.get(k) or '').strip() for k in ('collar', 'sleeve', 'color', 'note')):
        return True
    raw = cd.get('sizes_json') or ''
    if raw:
        try:
            import json as _json
            sizes = _json.loads(raw)
            if any((s.get('qty') or 0) > 0 for s in sizes if isinstance(s, dict)):
                return True
        except (ValueError, TypeError):
            pass
    return False


def _item_is_empty(item_form, variant_formset):
    """True if outer item has no image, no pk, and no real variant data — skip in validation."""
    if item_form.instance and item_form.instance.pk:
        return False
    if item_form.cleaned_data.get('design_image'):
        return False
    for v in variant_formset.forms:
        cd = getattr(v, 'cleaned_data', {}) or {}
        if cd.get('DELETE'):
            continue
        if _variant_has_real_content(v):
            return False
    return True


def _validate_variants_present(item_formset, variant_formsets):
    """Each surviving (non-deleted, non-empty) item must have ≥1 non-deleted variant
    with real content.

    Returns a list of (item_index, error_message) for the template to display.
    """
    errors = []
    for i, (item_form, vfs) in enumerate(zip(item_formset.forms, variant_formsets)):
        if not item_form.is_valid() or not vfs.is_valid():
            continue
        if item_form.cleaned_data.get('DELETE'):
            continue
        if _item_is_empty(item_form, vfs):
            continue
        live = [
            v for v in vfs.forms
            if not v.cleaned_data.get('DELETE') and _variant_has_real_content(v)
        ]
        if not live:
            errors.append((i, f'รายการที่ {i + 1} ต้องมีอย่างน้อย 1 แบบ'))
    return errors


def _save_with_variants(form, item_formset, variant_formsets, request, *, set_created_date):
    """Persist Order + items + variants. Caller has confirmed everything is valid."""
    order = form.save(commit=False)
    if set_created_date and not order.created_date:
        order.created_date = timezone.now().date()
    order.save()
    form.save_m2m()

    item_formset.instance = order
    item_formset.save()

    # Re-bind & save each inner formset, now that its parent OrderItem has a pk.
    for vfs, item_form in zip(variant_formsets, item_formset.forms):
        if item_form.cleaned_data.get('DELETE'):
            continue
        if not item_form.instance.pk:
            continue  # blank extra form
        vfs.instance = item_form.instance
        vfs.save()

    _copy_images_from_first(request, item_formset)
    return order


def _form_render_context(form, item_formset, variant_formsets, **extra):
    """Common template context — pairs items with their variant formsets."""
    items_with_variants = list(zip(item_formset.forms, variant_formsets))
    ctx = {
        'form': form,
        'formset': item_formset,
        'items_with_variants': items_with_variants,
        'empty_item_form': item_formset.empty_form,
        'empty_variant_formset': _empty_variant_formset(),
        'collar_suggestions': COLLAR_SUGGESTIONS,
        'sleeve_suggestions': SLEEVE_SUGGESTIONS,
    }
    ctx.update(extra)
    return ctx


@login_required
def order_create(request):
    if request.method == 'POST':
        form = OrderForm(request.POST)
        item_formset = OrderItemFormSet(request.POST, request.FILES, prefix='items')
        variant_formsets = _build_variant_formsets(item_formset, request.POST, request.FILES)

        forms_ok = form.is_valid() and item_formset.is_valid() and all(
            vfs.is_valid() for vfs in variant_formsets
        )
        variant_errors = _validate_variants_present(item_formset, variant_formsets)

        if forms_ok and not variant_errors:
            order = _save_with_variants(
                form, item_formset, variant_formsets, request, set_created_date=True,
            )
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm()
        item_formset = OrderItemFormSet(prefix='items')
        variant_formsets = _build_variant_formsets(item_formset)
        variant_errors = []

    return render(request, 'orders/order_form.html', _form_render_context(
        form, item_formset, variant_formsets,
        title='สร้างออร์เดอร์ใหม่',
        variant_errors=variant_errors,
    ))


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        item_formset = OrderItemFormSet(
            request.POST, request.FILES, instance=order, prefix='items',
        )
        variant_formsets = _build_variant_formsets(item_formset, request.POST, request.FILES)

        forms_ok = form.is_valid() and item_formset.is_valid() and all(
            vfs.is_valid() for vfs in variant_formsets
        )
        variant_errors = _validate_variants_present(item_formset, variant_formsets)

        if forms_ok and not variant_errors:
            _save_with_variants(
                form, item_formset, variant_formsets, request, set_created_date=False,
            )
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm(instance=order)
        item_formset = OrderItemFormSet(instance=order, prefix='items')
        variant_formsets = _build_variant_formsets(item_formset)
        variant_errors = []

    return render(request, 'orders/order_form.html', _form_render_context(
        form, item_formset, variant_formsets,
        order=order,
        title=f'แก้ไขออร์เดอร์ {order.order_number}',
        variant_errors=variant_errors,
    ))


@login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    return render(request, 'orders/order_detail.html', {'order': order})


@login_required
def order_print(request, pk):
    order = get_object_or_404(Order, pk=pk)
    items = list(order.items.all())

    # Split items into pages: page 1 has header so max 2 items, next pages max 3
    pages = []
    if items:
        pages.append(items[:2])   # First page: max 2 items (header takes space)
        remaining = items[2:]
        while remaining:
            pages.append(remaining[:3])  # Subsequent pages: max 3 items
            remaining = remaining[3:]

    # QR code → URL of the production-floor /update/ page for this order.
    # build_absolute_uri respects the request's scheme + host + FORCE_SCRIPT_NAME,
    # so the same code emits localhost URLs in dev and dr89.cloud URLs in prod.
    update_url = request.build_absolute_uri(
        reverse('update_order_stage', kwargs={'order_number': order.order_number})
    )
    qr_svg = generate_qr_svg(update_url)

    return render(request, 'orders/order_print.html', {
        'order': order,
        'pages': pages,
        'is_single_item': len(items) == 1,
        'qr_svg': qr_svg,
        'update_url': update_url,
    })


@login_required
@require_POST
def order_delete(request, pk):
    if not _is_admin(request.user):
        raise PermissionDenied
    order = get_object_or_404(Order, pk=pk)
    order.delete()
    return redirect('order_list')


# ---------------------------------------------------------------------------
# Production-channel views (cookie-based, no Django login)
# See CLAUDE-V1.6.md §1
# ---------------------------------------------------------------------------

def _safe_next(request, candidate):
    """Return candidate if it's a same-host relative path, else None."""
    if not candidate:
        return None
    if not url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return None
    return candidate


def select_department(request):
    if request.method == 'POST':
        slug = request.POST.get('department')
        if slug not in VALID_SLUGS:
            return redirect('select_department')

        next_url = (
            _safe_next(request, request.POST.get('next'))
            or reverse('dept_dashboard', kwargs={'slug': slug})
        )
        response = redirect(next_url)
        response.set_cookie(
            DEPT_COOKIE_NAME,
            slug,
            max_age=DEPT_COOKIE_MAX_AGE,
            httponly=True,
            samesite='Lax',
            secure=not settings.DEBUG,
        )
        return response

    return render(request, 'orders/select_department.html', {
        'departments': DEPARTMENTS,
        'next': _safe_next(request, request.GET.get('next')) or '',
        'current_slug': request.COOKIES.get(DEPT_COOKIE_NAME),
    })


def clear_department(request):
    response = redirect('select_department')
    response.delete_cookie(DEPT_COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Dashboard query config (Step 4)
#
# Each dept sees orders that have finished the previous stage but not yet
# their own. Print is the entry point, so it uses created_date.
# Repair (needs_repair=True) is shown separately to the print dept.
# Orders that finished pack (packed_at NOT NULL) drop out of every list.
# ---------------------------------------------------------------------------

DEPT_PENDING_CONFIG = {
    'print': {
        'filter': Q(print_done_at__isnull=True) & Q(needs_repair=False),
        'enter_field': 'created_date',
    },
    'roll': {
        'filter': Q(print_done_at__isnull=False) & Q(roll_done_at__isnull=True),
        'enter_field': 'print_done_at',
    },
    'cut': {
        'filter': Q(roll_done_at__isnull=False) & Q(cut_done_at__isnull=True),
        'enter_field': 'roll_done_at',
    },
    'sort': {
        'filter': Q(cut_done_at__isnull=False) & Q(sort_done_at__isnull=True) & Q(needs_repair=False),
        'enter_field': 'cut_done_at',
    },
    'sew': {
        'filter': Q(sort_done_at__isnull=False) & Q(sent_to_tailors_at__isnull=True),
        'enter_field': 'sort_done_at',
    },
    'pack': {
        'filter': Q(sent_to_tailors_at__isnull=False) & Q(packed_at__isnull=True),
        'enter_field': 'sent_to_tailors_at',
    },
}


def _format_waiting(when):
    """Return Thai short text like 'เพิ่งเข้า', '3 ชั่วโมง', '2 วัน'."""
    if not when:
        return ''
    # Promote DateField → aware datetime at start-of-day
    if not hasattr(when, 'hour'):
        dt = datetime.combine(when, time.min)
        when_aware = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    else:
        when_aware = when
    delta = timezone.now() - when_aware
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return 'เพิ่งเข้า'
    if hours < 24:
        return f'{int(hours)} ชั่วโมง'
    return f'{int(hours / 24)} วัน'


def _build_pending_rows(qs, enter_field, *, attr_override=None):
    """Materialize queryset into list of dicts the template can iterate cheaply.

    Uses the prefetch cache to grab the first item without an extra query.
    """
    rows = []
    for o in qs:
        items_cache = list(o.items.all())  # uses prefetch cache
        first_item = items_cache[0] if items_cache else None
        if attr_override:
            enter_at = getattr(o, attr_override)
        else:
            enter_at = getattr(o, enter_field)
        rows.append({
            'order': o,
            'first_item': first_item,
            'enter_at': enter_at,
            'waiting': _format_waiting(enter_at),
        })
    return rows


@require_department
def dept_dashboard(request, slug):
    dept = request.production_dept

    # --- Counters across the whole shop (one query each — keeps view simple) ---
    counters = []
    for d in DEPARTMENTS:
        cfg = DEPT_PENDING_CONFIG[d['slug']]
        counters.append({
            'slug': d['slug'],
            'name': d['name'],
            'icon': d['icon'],
            'color': d['color'],
            'count': Order.objects.filter(cfg['filter']).count(),
            'is_current': d['slug'] == dept['slug'],
        })
    repair_count = Order.objects.filter(needs_repair=True).count()

    # --- My pending orders (oldest first = most urgent at top) ---
    cfg = DEPT_PENDING_CONFIG[dept['slug']]
    pending_qs = (
        Order.objects.filter(cfg['filter'])
        .prefetch_related('items')
        .order_by(cfg['enter_field'], 'id')
    )
    pending = _build_pending_rows(pending_qs, cfg['enter_field'])

    # --- Repair queue (print dept only) ---
    repair_rows = []
    if dept['slug'] == 'print':
        latest_repair = StageLog.objects.filter(
            order=OuterRef('pk'),
            action='sort_repair',
        ).order_by('-created_at').values('created_at')[:1]

        repair_qs = (
            Order.objects.filter(needs_repair=True)
            .annotate(repair_requested_at=Subquery(latest_repair))
            .prefetch_related('items')
            .order_by('repair_requested_at', '-id')
        )
        repair_rows = _build_pending_rows(
            repair_qs, '', attr_override='repair_requested_at'
        )

    return render(request, 'orders/dept_dashboard.html', {
        'dept': dept,
        'counters': counters,
        'repair_count': repair_count,
        'pending': pending,
        'repair_rows': repair_rows,
    })


# ---------------------------------------------------------------------------
# Update view (Step 3) — fired by QR scan from the production floor
# ---------------------------------------------------------------------------

# Stages shown in the timeline (in workflow order)
STAGE_TIMELINE = [
    ('print', 'พิมพ์',     'print_done_at'),
    ('roll',  'โรล',        'roll_done_at'),
    ('cut',   'ตัด',        'cut_done_at'),
    ('sort',  'คัด',        'sort_done_at'),
    ('sew',   'ส่งเย็บ',     'sent_to_tailors_at'),
    ('pack',  'รีด+แพ็ค',  'packed_at'),
]


def _build_actions(order, dept_slug):
    """Return list of {key, label, color} buttons to render for this dept."""
    a = []
    if dept_slug == 'print':
        if order.print_done_at is None:
            a.append({'key': 'print_done', 'label': 'พิมพ์เสร็จ', 'color': 'success'})
        if order.needs_repair:
            a.append({'key': 'print_repair', 'label': 'ซ่อมเสร็จ', 'color': 'success'})
    elif dept_slug == 'roll':
        if order.roll_done_at is None:
            a.append({'key': 'roll_done', 'label': 'โรลเสร็จ', 'color': 'success'})
    elif dept_slug == 'cut':
        if order.cut_done_at is None:
            a.append({'key': 'cut_done', 'label': 'ตัดเสร็จ', 'color': 'success'})
    elif dept_slug == 'sort':
        if order.sort_done_at is None:
            a.append({'key': 'sort_done', 'label': 'ครบ', 'color': 'success'})
            a.append({'key': 'sort_repair', 'label': 'ส่งซ่อม', 'color': 'danger'})
    elif dept_slug == 'pack':
        if order.packed_at is None:
            a.append({'key': 'pack_done', 'label': 'รีดแพ็คแล้ว', 'color': 'success'})
        elif order.shipped_at is None and order.awaiting_pickup_at is None:
            a.append({'key': 'pack_shipped', 'label': 'ส่งแล้ว', 'color': 'info'})
            a.append({'key': 'pack_pickup', 'label': 'รอมารับ', 'color': 'warning'})
    return a


def _completed_for_dept(order, dept_slug):
    """Return list of (label, timestamp) showing what this dept already finished."""
    completed = []
    if dept_slug == 'print':
        if order.print_done_at:
            completed.append(('พิมพ์เสร็จ', order.print_done_at))
        if order.repair_done_at:
            completed.append(('ซ่อมเสร็จล่าสุด', order.repair_done_at))
    elif dept_slug == 'roll' and order.roll_done_at:
        completed.append(('โรลเสร็จ', order.roll_done_at))
    elif dept_slug == 'cut' and order.cut_done_at:
        completed.append(('ตัดเสร็จ', order.cut_done_at))
    elif dept_slug == 'sort' and order.sort_done_at:
        completed.append(('คัดครบ', order.sort_done_at))
    elif dept_slug == 'sew' and order.sent_to_tailors_at:
        completed.append(('ส่งเย็บแล้ว', order.sent_to_tailors_at))
    elif dept_slug == 'pack':
        if order.packed_at:
            completed.append(('รีดแพ็คแล้ว', order.packed_at))
        if order.shipped_at:
            completed.append(('ส่งแล้ว', order.shipped_at))
        if order.awaiting_pickup_at:
            completed.append(('รอมารับ', order.awaiting_pickup_at))
    return completed


def _apply_action(order, dept_slug, action, request):
    """Mutate order based on action. Returns (success, message)."""
    now = timezone.now()

    # (department, action) → handler logic
    if dept_slug == 'print' and action == 'print_done':
        if order.print_done_at:
            return False, 'รายการนี้ถูกบันทึก "พิมพ์เสร็จ" ไปแล้ว'
        order.print_done_at = now
        order.save(update_fields=['print_done_at'])
        StageLog.objects.create(order=order, department='print', action='print_done')
        return True, '✓ บันทึก "พิมพ์เสร็จ" แล้ว'

    if dept_slug == 'print' and action == 'print_repair':
        if not order.needs_repair:
            return False, 'ไม่มีงานซ่อมที่ค้างอยู่'
        order.needs_repair = False
        order.repair_done_at = now
        order.save(update_fields=['needs_repair', 'repair_done_at'])
        StageLog.objects.create(order=order, department='print', action='print_repair')
        return True, '✓ บันทึก "ซ่อมเสร็จ" แล้ว'

    if dept_slug == 'roll' and action == 'roll_done':
        if order.roll_done_at:
            return False, 'รายการนี้ถูกบันทึกไปแล้ว'
        order.roll_done_at = now
        order.save(update_fields=['roll_done_at'])
        StageLog.objects.create(order=order, department='roll', action='roll_done')
        return True, '✓ บันทึก "โรลเสร็จ" แล้ว'

    if dept_slug == 'cut' and action == 'cut_done':
        if order.cut_done_at:
            return False, 'รายการนี้ถูกบันทึกไปแล้ว'
        order.cut_done_at = now
        order.save(update_fields=['cut_done_at'])
        StageLog.objects.create(order=order, department='cut', action='cut_done')
        return True, '✓ บันทึก "ตัดเสร็จ" แล้ว'

    if dept_slug == 'sort' and action == 'sort_done':
        if order.sort_done_at:
            return False, 'รายการนี้ถูกบันทึกไปแล้ว'
        order.sort_done_at = now
        order.save(update_fields=['sort_done_at'])
        StageLog.objects.create(order=order, department='sort', action='sort_done')
        return True, '✓ บันทึก "ครบ" แล้ว'

    if dept_slug == 'sort' and action == 'sort_repair':
        if order.needs_repair:
            return False, 'งานนี้ส่งซ่อมไปแล้ว รอแผนกพิมพ์'
        order.needs_repair = True
        order.save(update_fields=['needs_repair'])
        StageLog.objects.create(order=order, department='sort', action='sort_repair')
        return True, '✓ ส่งซ่อมแล้ว — รอแผนกพิมพ์'

    if dept_slug == 'sew' and action == 'sew_send':
        if order.sent_to_tailors_at:
            return False, 'งานนี้ถูกส่งให้คนเย็บไปแล้ว'
        tailor_ids = request.POST.getlist('tailors')
        if not tailor_ids:
            return False, 'กรุณาเลือกคนเย็บอย่างน้อย 1 คน'
        tailors = list(Tailor.objects.filter(id__in=tailor_ids, is_active=True))
        if not tailors:
            return False, 'คนเย็บที่เลือกไม่ถูกต้อง'
        order.sent_to_tailors_at = now
        order.save(update_fields=['sent_to_tailors_at'])
        order.tailors.set(tailors)
        names = ', '.join(t.name for t in tailors)
        StageLog.objects.create(
            order=order, department='sew', action='sew_send',
            note=f'ส่งให้: {names}',
        )
        return True, f'✓ ส่งให้คนเย็บแล้ว ({names})'

    if dept_slug == 'pack' and action == 'pack_done':
        if order.packed_at:
            return False, 'รายการนี้ถูกบันทึกไปแล้ว'
        order.packed_at = now
        order.save(update_fields=['packed_at'])
        StageLog.objects.create(order=order, department='pack', action='pack_done')
        return True, '✓ บันทึก "รีดแพ็คแล้ว" แล้ว'

    if dept_slug == 'pack' and action == 'pack_shipped':
        if not order.packed_at:
            return False, 'ต้องรีดแพ็คก่อน'
        if order.shipped_at or order.awaiting_pickup_at:
            return False, 'งานนี้ถูกบันทึกไปแล้ว'
        order.shipped_at = now
        order.save(update_fields=['shipped_at'])
        StageLog.objects.create(order=order, department='pack', action='pack_shipped')
        return True, '✓ บันทึก "ส่งแล้ว"'

    if dept_slug == 'pack' and action == 'pack_pickup':
        if not order.packed_at:
            return False, 'ต้องรีดแพ็คก่อน'
        if order.shipped_at or order.awaiting_pickup_at:
            return False, 'งานนี้ถูกบันทึกไปแล้ว'
        order.awaiting_pickup_at = now
        order.save(update_fields=['awaiting_pickup_at'])
        StageLog.objects.create(order=order, department='pack', action='pack_pickup')
        return True, '✓ บันทึก "รอมารับ"'

    return False, 'การกระทำนี้ไม่ถูกต้องสำหรับแผนกของคุณ'


@require_department
def update_order_stage(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    dept = request.production_dept

    if request.method == 'POST':
        action = request.POST.get('action', '')
        ok, msg = _apply_action(order, dept['slug'], action, request)
        (messages.success if ok else messages.error)(request, msg)
        return redirect('update_order_stage', order_number=order_number)

    actions = _build_actions(order, dept['slug'])
    completed = _completed_for_dept(order, dept['slug'])

    timeline = []
    for slug, label, field in STAGE_TIMELINE:
        ts = getattr(order, field)
        if ts:
            timeline.append({'slug': slug, 'label': label, 'timestamp': ts})

    tailors = (
        Tailor.objects.filter(is_active=True)
        if dept['slug'] == 'sew' and order.sent_to_tailors_at is None
        else None
    )

    first_item = order.items.first()

    return render(request, 'orders/update_order_stage.html', {
        'order': order,
        'dept': dept,
        'actions': actions,
        'completed': completed,
        'timeline': timeline,
        'tailors': tailors,
        'first_item': first_item,
        'order_tailors': list(order.tailors.all()) if order.sent_to_tailors_at else [],
    })
