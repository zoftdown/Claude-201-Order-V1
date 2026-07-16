import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import (
    require_department, viewer_or_login_required,
    DEPT_COOKIE_NAME, DEPT_PIN_HASH_COOKIE,
)
from .departments import DEPARTMENTS, VALID_SLUGS, VIEWER_SLUG, get_department
from .forms import (
    OrderForm,
    OrderItemFormSet,
    ShirtVariantFormSet,
    COLLAR_SUGGESTIONS,
    SLEEVE_SUGGESTIONS,
)
from .models import (
    Customer, CustomerPrice, CustomerTag, DepartmentPIN, ExtraImage,
    ExtraNameRow, MasterImage, Order, StageLog, Tailor,
)
from .qr_utils import generate_qr_svg

DEPT_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year


def _is_admin(user):
    return user.is_superuser or user.groups.filter(name='admin').exists()


@viewer_or_login_required
def order_list(request):
    # Pure date order (urgent NOT pulled to the very top here). The template
    # shows urgent orders twice: in a bordered "งานด่วน" zone at the top AND
    # in their natural date slot below (so they're not missed when working
    # through the list by date). prefetch items__variants kills the N+1 that
    # Order.total_qty / OrderItem.total_qty would otherwise trigger per row.
    orders = (
        Order.objects.prefetch_related('items', 'items__variants')
        .select_related('created_by')
        # child_count: badge "งานชุด" ในแถว (นับใบเพิ่มของใบนี้) โดยไม่ N+1
        .annotate(child_count=Count('child_orders', distinct=True))
        .order_by('-created_date', '-id')
    )

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

    # Evaluate once; derive the urgent subset in Python so the urgent zone
    # costs no extra DB query (and reuses the same prefetched objects).
    orders = list(orders)
    urgent_orders = [o for o in orders if o.is_urgent]

    return render(request, 'orders/order_list.html', {
        'orders': orders,
        'urgent_orders': urgent_orders,
        'current_status': status,
        'search_query': q or '',
        'status_choices': Order.STATUS_CHOICES,
    })


# Stage-timestamp fields (null = ยังไม่ถึง stage นั้น). Used by the "ค้างเกิน
# 7 วัน" report to detect orders with zero production progress. Mirrors the
# fields on Order (Phase 1.6) — keep in sync if stages change.
STAGE_TIMESTAMP_FIELDS = (
    'print_done_at', 'roll_done_at', 'cut_done_at', 'sort_done_at',
    'sent_to_tailors_at', 'packed_at', 'shipped_at', 'awaiting_pickup_at',
)


def _daily_summary_context(request):
    """Build the สรุปใบงานรายวัน context for a given ?date=. Shared by the
    standalone daily_summary page and the reports dashboard's first tab, so the
    summary logic lives in exactly one place. prefetch items__variants ตัด N+1
    ของ total_qty (เหมือน order_list)."""
    today = timezone.localdate()
    day = parse_date(request.GET.get('date', '') or '') or today

    orders = list(
        Order.objects.filter(created_date=day)
        .prefetch_related('items', 'items__variants')
        .order_by('-is_urgent', '-id')
    )
    total_qty = sum(o.total_qty for o in orders)

    return {
        'day': day,
        'today': today,
        'orders': orders,
        'order_count': len(orders),
        'total_qty': total_qty,
        'prev_date': day - timedelta(days=1),
        'next_date': day + timedelta(days=1),
    }


@viewer_or_login_required
def daily_summary(request):
    """สรุปใบงานรายวัน — หัวหน้างานเปิดดูออร์เดอร์ของวันหนึ่งๆ รวมกัน เพื่อกัน
    ออร์เดอร์ตกหล่น. ?date=YYYY-MM-DD (ไม่ระบุ/ค่าผิด = วันนี้)."""
    return render(request, 'orders/daily_summary.html', _daily_summary_context(request))


# ---------------------------------------------------------------------------
# Reports dashboard (admin-only) — sidebar เลือกรายงาน, เนื้อหาฝั่งขวา.
# Tab แรก = สรุปรายวัน (reuse daily_summary content). Mounted at /order/reports/.
# ---------------------------------------------------------------------------

REPORT_TABS = [
    ('daily', '📋 สรุปรายวัน'),
    ('stuck', '⏳ ค้างเกิน 7 วัน'),
    ('over200', '📦 เกิน 200 ตัว'),
    ('stats', '📈 สถิติร้าน'),
]

THAI_MONTHS_SHORT = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.',
                     'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.']


def _report_stats_context():
    """สถิติร้าน 12 เดือนล่าสุด (เฟส 5 dashboard): รายเดือน (ใบ/ตัว/ยอดเงิน),
    แยกตามแหล่งที่มา, ลูกค้า top 10. รวมยอดใน app layer รอบเดียว เพราะ
    total_qty อยู่ใน ShirtVariant.sizes (JSON) นับใน DB ไม่ได้ — prefetch
    items__variants ตัด N+1 เหมือน report อื่น. group ลูกค้าด้วย customer_name
    (ข้อความ) เพื่อให้ครอบคลุมใบเก่าที่ไม่มีโปรไฟล์ Customer ด้วย."""
    today = timezone.localdate()
    month_keys = []
    for i in range(11, -1, -1):
        mm, yy = today.month - i, today.year
        while mm <= 0:
            mm += 12
            yy -= 1
        month_keys.append((yy, mm))
    start = date(month_keys[0][0], month_keys[0][1], 1)

    buckets = {k: {'count': 0, 'qty': 0, 'revenue': 0.0} for k in month_keys}
    source_stats = {}
    customer_stats = {}

    orders = (
        Order.objects.filter(created_date__gte=start)
        .prefetch_related('items', 'items__variants')
    )
    for o in orders:
        key = (o.created_date.year, o.created_date.month)
        if key not in buckets:
            continue
        qty = o.total_qty
        revenue = float(o.total_price or 0)
        b = buckets[key]
        b['count'] += 1
        b['qty'] += qty
        b['revenue'] += revenue
        s = source_stats.setdefault(o.source, {'count': 0, 'qty': 0, 'revenue': 0.0})
        s['count'] += 1
        s['qty'] += qty
        s['revenue'] += revenue
        cname = (o.customer_name or '').strip() or '(ไม่ระบุชื่อ)'
        c = customer_stats.setdefault(cname, {'count': 0, 'qty': 0, 'revenue': 0.0})
        c['count'] += 1
        c['qty'] += qty
        c['revenue'] += revenue

    # label เดือนแบบไทย เช่น "ก.ค. 69" (ปี พ.ศ. 2 หลัก — ชุดเดียวกับเลข order)
    month_labels = [
        f'{THAI_MONTHS_SHORT[mm - 1]} {(yy + 543) % 100:02d}' for yy, mm in month_keys
    ]
    monthly = [buckets[k] for k in month_keys]
    source_rows = sorted(
        ({'source': k, **v} for k, v in source_stats.items()),
        key=lambda r: r['revenue'], reverse=True,
    )
    top_customers = sorted(
        ({'name': k, **v} for k, v in customer_stats.items()),
        key=lambda r: r['revenue'], reverse=True,
    )[:10]

    this_month = buckets[month_keys[-1]]
    avg_per_order = (this_month['revenue'] / this_month['count']) if this_month['count'] else 0

    return {
        'stats_chart_data': {
            'labels': month_labels,
            'revenue': [round(b['revenue']) for b in monthly],
            'counts': [b['count'] for b in monthly],
            'qtys': [b['qty'] for b in monthly],
            'source_labels': [r['source'] for r in source_rows],
            'source_revenue': [round(r['revenue']) for r in source_rows],
        },
        'stats_this_month': this_month,
        'stats_avg_per_order': avg_per_order,
        'stats_month_label': month_labels[-1],
        'stats_source_rows': source_rows,
        'stats_top_customers': top_customers,
        'stats_monthly_rows': list(zip(month_labels, monthly)),
        'stats_start': start,
    }


# Statuses ที่ถือว่า "ขยับแล้ว" → ตัดออกจากรายงานค้าง แม้ stage timestamp จะ null
# (ใบที่ตั้ง status เองโดยไม่ได้กดผ่านระบบ stage QR). เหลือเฉพาะ "รอดำเนินการ".
STUCK_EXCLUDE_STATUSES = ('กำลังผลิต', 'เสร็จแล้ว', 'ส่งแล้ว')


def _report_stuck_rows(sort='date_desc'):
    """ออร์เดอร์ที่ค้างจริง: created_date เกิน 7 วัน + ทุก stage timestamp ยัง null
    (ยังไม่เริ่มแม้แต่พิมพ์) + status ยังไม่ขยับ (ตัด กำลังผลิต/เสร็จแล้ว/ส่งแล้ว ออก).
    sort='date_desc' (ใหม่สุดก่อน, default) หรือ 'date_asc'. + จำนวนวันที่ค้าง."""
    today = timezone.localdate()
    cutoff = today - timedelta(days=7)
    qs = Order.objects.filter(created_date__lte=cutoff).exclude(status__in=STUCK_EXCLUDE_STATUSES)
    for field in STAGE_TIMESTAMP_FIELDS:
        qs = qs.filter(**{f'{field}__isnull': True})
    date_order = 'created_date' if sort == 'date_asc' else '-created_date'
    qs = qs.order_by(date_order, '-id')
    return [{'order': o, 'days_stuck': (today - o.created_date).days} for o in qs]


def _report_over200_rows():
    """ออร์เดอร์ที่ผลรวมจำนวนเสื้อ (รวม qty ทุก ShirtVariant.sizes) > 200.
    total_qty เป็น Python property → ต้องนับใน app layer (prefetch items__variants
    ตัด N+1). มากสุดขึ้นก่อน."""
    orders = (
        Order.objects.prefetch_related('items', 'items__variants')
        .order_by('-created_date', '-id')
    )
    rows = [{'order': o, 'qty': o.total_qty} for o in orders]
    rows = [r for r in rows if r['qty'] > 200]
    rows.sort(key=lambda r: r['qty'], reverse=True)
    return rows


@login_required
def reports(request):
    _require_admin(request.user)

    report = request.GET.get('report', 'daily')
    if report not in dict(REPORT_TABS):
        report = 'daily'

    ctx = {'report_tabs': REPORT_TABS, 'current_report': report}
    if report == 'daily':
        ctx.update(_daily_summary_context(request))
    elif report == 'stuck':
        stuck_sort = 'date_asc' if request.GET.get('sort') == 'date_asc' else 'date_desc'
        ctx['stuck_sort'] = stuck_sort
        ctx['stuck_rows'] = _report_stuck_rows(stuck_sort)
    elif report == 'over200':
        ctx['over200_rows'] = _report_over200_rows()
    elif report == 'stats':
        # หน้าสถิติมีรหัสอีกชั้น (นอกจาก login+admin) เพราะเป็นยอดขายรวมของร้าน.
        # ใส่ถูกครั้งเดียว → จำใน session (หมดเมื่อ logout/session หมดอายุ)
        if not request.session.get('stats_unlocked'):
            if request.method == 'POST':
                from django.utils.crypto import constant_time_compare
                pin = (request.POST.get('stats_pin') or '').strip()
                if constant_time_compare(pin, settings.STATS_PIN):
                    request.session['stats_unlocked'] = True
                else:
                    ctx['stats_pin_error'] = True
        if request.session.get('stats_unlocked'):
            ctx.update(_report_stats_context())
        else:
            ctx['stats_locked'] = True

    return render(request, 'orders/reports.html', ctx)


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


def _save_master_images(request, order):
    """Persist รูปมาสเตอร์ for an order: delete any checked existing images,
    then append all newly uploaded files. Independent of the OrderItem flow —
    the form renders one single-file <input name="master_images"> per slot
    (Choose-file or clipboard-paste). getlist() gathers the files from every
    filled slot at once, so empty slots are skipped automatically; per-image
    delete checkboxes remove existing ones. Not a formset."""
    delete_ids = request.POST.getlist('delete_master')
    if delete_ids:
        order.master_images.filter(pk__in=delete_ids).delete()

    new_files = request.FILES.getlist('master_images')
    if new_files:
        start = order.master_images.aggregate(m=Max('order_index'))['m'] or 0
        for offset, f in enumerate(new_files, start=1):
            MasterImage.objects.create(order=order, image=f, order_index=start + offset)


def _save_signed_image(request, order):
    """Attach / replace / remove the single signed-copy photo (รูปที่เซ็นแล้ว).
    A checked 'delete_signed' removes the current one; a new 'signed_image' upload
    replaces it. Order.save() auto-downscales (reuses downscale_image_field)."""
    if request.POST.get('delete_signed') and order.signed_image:
        order.signed_image.delete(save=False)  # drop the file off disk
        order.save(update_fields=['signed_image'])
        return
    f = request.FILES.get('signed_image')
    if f:
        order.signed_image = f
        order.save(update_fields=['signed_image'])  # triggers downscale


def _save_extra_images(request, order):
    """Persist รูปเพิ่มเติม (ExtraImage) for an order. Same multi-slot flow as
    _save_master_images: delete any checked existing images, then append all
    newly uploaded files from the <input name="extra_images"> slots. Empty slots
    are skipped automatically by getlist(); ExtraImage.save() auto-downscales."""
    delete_ids = request.POST.getlist('delete_extra')
    if delete_ids:
        order.extra_images.filter(pk__in=delete_ids).delete()

    new_files = request.FILES.getlist('extra_images')
    if new_files:
        start = order.extra_images.aggregate(m=Max('order_index'))['m'] or 0
        for offset, f in enumerate(new_files, start=1):
            ExtraImage.objects.create(order=order, image=f, order_index=start + offset)


def _save_extra_name_rows(request, order):
    """Rebuild the order's รันชื่อ-เบอร์ table from the parallel POST arrays
    (extra_size[], extra_number[], extra_name[]). Wipe-and-recreate keeps it
    simple; rows where all three fields are blank are skipped."""
    sizes = request.POST.getlist('extra_size')
    numbers = request.POST.getlist('extra_number')
    names = request.POST.getlist('extra_name')
    order.extra_name_rows.all().delete()
    rows = []
    for i in range(max(len(sizes), len(numbers), len(names))):
        s = (sizes[i] if i < len(sizes) else '').strip()
        n = (numbers[i] if i < len(numbers) else '').strip()
        nm = (names[i] if i < len(names) else '').strip()
        if not (s or n or nm):
            continue
        rows.append(ExtraNameRow(order=order, size=s, number=n, name=nm, order_index=i))
    if rows:
        ExtraNameRow.objects.bulk_create(rows)


def _resolve_customer(request, order):
    """หาโปรไฟล์ Customer ให้ใบงานนี้ (เฟส 1 CRM).

    ลำดับ: customer_id ที่เลือกจาก autocomplete → match ชื่อ+ลิงก์ตรงเป๊ะกับ
    โปรไฟล์เดิม → สร้างโปรไฟล์ใหม่. customer_name/customer_link บนใบงาน
    ยังเป็นข้อความอิสระเหมือนเดิม — โปรไฟล์เป็นแค่ตัวเชื่อม."""
    cid = (request.POST.get('customer_id') or '').strip()
    if cid.isdigit():
        picked = Customer.objects.filter(pk=int(cid)).first()
        if picked:
            return picked
    name = (order.customer_name or '').strip()
    if not name:
        return None
    link = (order.customer_link or '').strip()
    existing = (Customer.objects
                .filter(name=name, facebook_link=link)
                .order_by('id').first())
    if existing:
        return existing
    return Customer.objects.create(name=name, facebook_link=link)


def _apply_brief_job(request, order):
    """เซ็ต order.brief_job_id จาก hidden field (เฟส 3 เชื่อมระบบ Brief).
    JS เซ็ตเมื่อเลือกจาก autocomplete / เคลียร์เมื่อพิมพ์เลขเอง — ค่าว่าง = ไม่ผูก."""
    raw = (request.POST.get('brief_job_id') or '').strip()
    order.brief_job_id = int(raw) if raw.isdigit() else None


def _push_order_ref_to_brief(order):
    """ยิงเลขออร์เดอร์กลับไปเซ็ต Job.order_ref ฝั่ง Brief (ลิงก์สองทาง).
    best-effort: Brief ล่ม/ยังไม่ตั้ง token → ข้ามเงียบ ไม่ block การ save ใบงาน."""
    if not (order.brief_job_id and order.design_doc_number):
        return
    import urllib.parse
    import urllib.request

    url = f'{settings.BRIEF_API_BASE}/api/jobs/{order.brief_job_id}/order-ref/'
    data = urllib.parse.urlencode({'order_ref': order.order_number}).encode()
    headers = {}
    if settings.BRIEF_API_TOKEN:
        headers['X-Api-Token'] = settings.BRIEF_API_TOKEN
    try:
        req = urllib.request.Request(url, data=data, headers=headers)
        urllib.request.urlopen(req, timeout=3)
    except OSError:
        pass


def _save_with_variants(form, item_formset, variant_formsets, request, *, set_created_date):
    """Persist Order + items + variants. Caller has confirmed everything is valid."""
    order = form.save(commit=False)
    order.customer = _resolve_customer(request, order)
    _apply_brief_job(request, order)
    if set_created_date and not order.created_date:
        order.created_date = timezone.now().date()
    # Record who created the order — only on create, never overwrite on edit.
    if set_created_date and request.user.is_authenticated:
        order.created_by = request.user
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
    _save_master_images(request, order)
    _save_signed_image(request, order)
    _save_extra_images(request, order)
    _save_extra_name_rows(request, order)
    _push_order_ref_to_brief(order)
    return order


def _customer_prices_payload(customer):
    """ตารางราคาของลูกค้าเป็น list ที่ json_script ใช้ได้ — [] เมื่อไม่ผูกลูกค้า."""
    if not customer:
        return []
    return [{'label': p.label, 'price': float(p.price)} for p in customer.prices.all()]


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
        'customer_prices': [],
        'brief_public_base': settings.BRIEF_PUBLIC_BASE,
    }
    ctx.update(extra)
    return ctx


def _resolve_parent_order(raw_pk):
    """หา "ใบแรกของชุด" (root) จาก pk ที่ส่งมา — ใบเพิ่มชี้ root เสมอ:
    สร้างใบเพิ่มจากใบที่เป็นใบเพิ่มอยู่แล้ว → flatten ไปชี้ root เดิม."""
    raw_pk = str(raw_pk or '').strip()
    if not raw_pk.isdigit():
        return None
    parent = Order.objects.filter(pk=int(raw_pk)).first()
    if parent and parent.parent_order_id:
        parent = parent.parent_order
    return parent


@login_required
def order_create(request):
    is_admin = _is_admin(request.user)
    # เฟส 2: "สร้างใบเพิ่มจากใบนี้" — GET ?from=<pk> เติมข้อมูลชุดเดิม +
    # hidden parent_order_id ใน form พากลับมาตอน POST
    parent_order = _resolve_parent_order(
        request.POST.get('parent_order_id') if request.method == 'POST'
        else request.GET.get('from')
    )
    if request.method == 'POST':
        form = OrderForm(request.POST, is_admin=is_admin)
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
            if parent_order and parent_order.pk != order.pk:
                order.parent_order = parent_order
                order.save(update_fields=['parent_order'])
            return redirect('order_detail', pk=order.pk)
    else:
        initial = {}
        if parent_order:
            # ข้อมูลที่ "ชุดเดียวกัน" ใช้ร่วม — ไม่ก๊อปเงิน/คำสั่งพิเศษ (ของใครของมัน)
            initial = {
                'source': parent_order.source,
                'production_place': parent_order.production_place,
                'customer_name': parent_order.customer_name,
                'customer_link': parent_order.customer_link,
                'shirt_name': parent_order.shirt_name,
                'designer_name': parent_order.designer_name,
                'design_doc_number': parent_order.design_doc_number,
                'fabric_spec': parent_order.fabric_spec,
                'delivery_method': parent_order.delivery_method,
            }
        form = OrderForm(is_admin=is_admin, initial=initial)
        item_formset = OrderItemFormSet(prefix='items')
        variant_formsets = _build_variant_formsets(item_formset)
        variant_errors = []

    return render(request, 'orders/order_form.html', _form_render_context(
        form, item_formset, variant_formsets,
        title='สร้างออร์เดอร์ใหม่',
        variant_errors=variant_errors,
        is_admin=is_admin,
        parent_order=parent_order,
        customer_prices=_customer_prices_payload(parent_order.customer) if parent_order else [],
    ))


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    is_admin = _is_admin(request.user)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order, is_admin=is_admin)
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
        form = OrderForm(instance=order, is_admin=is_admin)
        item_formset = OrderItemFormSet(instance=order, prefix='items')
        variant_formsets = _build_variant_formsets(item_formset)
        variant_errors = []

    return render(request, 'orders/order_form.html', _form_render_context(
        form, item_formset, variant_formsets,
        order=order,
        title=f'แก้ไขออร์เดอร์ {order.order_number}',
        variant_errors=variant_errors,
        is_admin=is_admin,
        customer_prices=_customer_prices_payload(order.customer),
    ))


def _build_detail_timeline(order):
    """Stage progress widget for the detail page.

    Returns one dict per stage with 'state' in {done, current, pending}.
    The first stage without a timestamp becomes 'current'; later empty
    stages are 'pending'. The terminal stage depends on delivery_method
    so we never show both shipped + awaiting_pickup at once.
    """
    stages = [
        ('พิมพ์',     order.print_done_at),
        ('โรล',       order.roll_done_at),
        ('ตัด',       order.cut_done_at),
        ('คัด',       order.sort_done_at),
        ('ส่งเย็บ',    order.sent_to_tailors_at),
        ('รีด+แพ็ค', order.packed_at),
    ]
    if order.delivery_method == 'ส่ง':
        stages.append(('ส่งของ', order.shipped_at))
    else:
        stages.append(('ลูกค้ารับ', order.awaiting_pickup_at))

    timeline = []
    current_seen = False
    for label, ts in stages:
        if ts:
            state = 'done'
        elif not current_seen:
            state = 'current'
            current_seen = True
        else:
            state = 'pending'
        timeline.append({'label': label, 'timestamp': ts, 'state': state})
    return timeline


@viewer_or_login_required
def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    return render(request, 'orders/order_detail.html', {
        'order': order,
        'stage_timeline': _build_detail_timeline(order),
        'group_orders': order.group_orders(),
        'brief_public_base': settings.BRIEF_PUBLIC_BASE,
    })


@viewer_or_login_required
def order_print(request, pk):
    order = get_object_or_404(Order, pk=pk)
    # Phase 1.7: one item per physical page via CSS page-break.
    # Template chunks variants into first-4 + remainder for items with 5+ variants.
    items = list(order.items.prefetch_related('variants').all())

    # QR code → URL of the production-floor /update/ page for this order.
    # build_absolute_uri respects the request's scheme + host + FORCE_SCRIPT_NAME,
    # so the same code emits localhost URLs in dev and dr89.cloud URLs in prod.
    update_url = request.build_absolute_uri(
        reverse('update_order_stage', kwargs={'order_number': order.order_number})
    )
    qr_svg = generate_qr_svg(update_url)

    return render(request, 'orders/order_print.html', {
        'order': order,
        'items': items,
        'qr_svg': qr_svg,
        'update_url': update_url,
        'group_orders': order.group_orders(),
    })


@login_required
@require_POST
def order_mark_printed(request, pk):
    """Mark the work-order sheet as printed (from the print page button)."""
    order = get_object_or_404(Order, pk=pk)
    order.printed_at = timezone.now()
    order.save(update_fields=['printed_at'])
    return redirect('order_print', pk=order.pk)


@viewer_or_login_required
def order_pick(request, pk):
    """ใบคัด (pick/sorting sheet): one OrderItem per A4 page, big design image
    + variant tables. Separate view/template from order_print so the work-order
    sheet (4 copies + save-image) is untouched. No QR/price/customer info."""
    order = get_object_or_404(Order, pk=pk)
    items = list(order.items.prefetch_related('variants').all())
    return render(request, 'orders/order_pick.html', {
        'order': order,
        'items': items,
    })


@viewer_or_login_required
def order_master(request, pk):
    """ใบมาสเตอร์ (master sheet): แสดงรูปมาสเตอร์ใหญ่ๆ ทุกรูป ไล่ลงมา + ช่องเซ็นตรวจ
    (วันที่/กราฟิก/วางพิมพ์/เลเซอร์/คนคัด/ลูกค้า). ไม่มี QR/ราคา. ปุ่มควบคุมอยู่นอก
    div ที่พิมพ์ (เหมือน order_print/order_pick)."""
    order = get_object_or_404(Order, pk=pk)
    master_images = list(order.master_images.all())
    return render(request, 'orders/order_master.html', {
        'order': order,
        'master_images': master_images,
    })


@viewer_or_login_required
def order_extra_csv(request, pk):
    """Export the order's รันชื่อ-เบอร์ table (ExtraNameRow) as CSV for the
    nesting software. UTF-8 + BOM so Excel/the nesting tool read Thai correctly."""
    import csv
    order = get_object_or_404(Order, pk=pk)
    # charset=utf-8 (NOT utf-8-sig): Django re-encodes every resp.write() with
    # the response charset, and utf-8-sig prepends a BOM on each call → a BOM
    # before every CSV row. We write one explicit BOM below instead.
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{order.order_number}_names.csv"'
    resp.write('﻿')  # one BOM ให้ Excel/โปรแกรม nesting อ่านไทยถูก
    w = csv.writer(resp)
    w.writerow(['ไซส์', 'เบอร์', 'ชื่อ'])
    for r in order.extra_name_rows.all():
        w.writerow([r.size, r.number, r.name])
    return resp


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


def _set_dept_cookies(response, slug):
    """Set both dept slug + pin hash cookies with shared options."""
    common = dict(
        max_age=DEPT_COOKIE_MAX_AGE,
        httponly=True,
        samesite='Lax',
        secure=not settings.DEBUG,
    )
    response.set_cookie(DEPT_COOKIE_NAME, slug, **common)
    response.set_cookie(DEPT_PIN_HASH_COOKIE, DepartmentPIN.current_hash(), **common)


def _landing_for(slug):
    if slug == VIEWER_SLUG:
        return reverse('order_list')
    return reverse('dept_dashboard', kwargs={'slug': slug})


def select_department(request):
    """Two-step flow:

    Step 1  GET                       → render the dept grid.
    Step 2  POST with dept only       → render the PIN entry page for that dept.
    Step 3  POST with dept + pin OK   → set cookies, redirect to dept's landing.
    Step 3' POST with dept + bad pin  → re-render PIN entry with error.
    """
    next_url_get = _safe_next(request, request.GET.get('next')) or ''
    expired = request.GET.get('reason') == 'pin_expired'

    if request.method == 'POST':
        slug = request.POST.get('department')
        if slug not in VALID_SLUGS:
            return redirect('select_department')

        pin = (request.POST.get('pin') or '').strip()
        next_url = _safe_next(request, request.POST.get('next')) or ''

        # Step 2: dept chosen, no PIN yet → show PIN form.
        if not pin:
            return render(request, 'orders/select_department.html', {
                'departments': DEPARTMENTS,
                'pending_dept': get_department(slug),
                'next': next_url,
                'pin_error': None,
                'expired': False,
            })

        # Step 3: verify PIN.
        if not DepartmentPIN.verify(pin):
            return render(request, 'orders/select_department.html', {
                'departments': DEPARTMENTS,
                'pending_dept': get_department(slug),
                'next': next_url,
                'pin_error': 'PIN ไม่ถูกต้อง',
                'expired': False,
            })

        landing = next_url or _landing_for(slug)
        response = redirect(landing)
        _set_dept_cookies(response, slug)
        return response

    return render(request, 'orders/select_department.html', {
        'departments': DEPARTMENTS,
        'pending_dept': None,
        'next': next_url_get,
        'pin_error': None,
        'expired': expired,
        'current_slug': request.COOKIES.get(DEPT_COOKIE_NAME),
    })


def clear_department(request):
    response = redirect('select_department')
    response.delete_cookie(DEPT_COOKIE_NAME)
    response.delete_cookie(DEPT_PIN_HASH_COOKIE)
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


def _build_search_rows(qs, dept_slug):
    """Like _build_pending_rows but adds the primary stage-done action
    for the current dept, so the search list can render an in-line button."""
    rows = []
    for o in qs:
        items_cache = list(o.items.all())
        first_item = items_cache[0] if items_cache else None
        actions = _build_actions(o, dept_slug)
        rows.append({
            'order': o,
            'first_item': first_item,
            'primary_action': actions[0] if actions else None,
        })
    return rows


@require_department
def dept_dashboard(request, slug):
    dept = request.production_dept

    # Viewer dept has no production-stage queue — send to the order list.
    if dept['slug'] == VIEWER_SLUG:
        return redirect('order_list')

    # --- Counters across the whole shop (one query each — keeps view simple) ---
    counters = []
    for d in DEPARTMENTS:
        cfg = DEPT_PENDING_CONFIG.get(d['slug'])
        if cfg is None:
            continue  # non-production dept (e.g. viewer) has no queue
        counters.append({
            'slug': d['slug'],
            'name': d['name'],
            'icon': d['icon'],
            'color': d['color'],
            'count': Order.objects.filter(cfg['filter']).count(),
            'is_current': d['slug'] == dept['slug'],
        })
    repair_count = Order.objects.filter(needs_repair=True).count()

    # --- Search (any order, not restricted to dept's pending queue) ---
    q = (request.GET.get('q') or '').strip()
    search_rows = []
    if q:
        search_qs = (
            Order.objects
            .filter(
                Q(customer_name__icontains=q)
                | Q(order_number__icontains=q)
                | Q(shirt_name__icontains=q)
            )
            .prefetch_related('items')
            .order_by('-id')[:50]
        )
        search_rows = _build_search_rows(search_qs, dept['slug'])

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
        'search_query': q,
        'search_rows': search_rows,
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


# ---------------------------------------------------------------------------
# User management (admin-only) — list/add/edit-password/delete
# Mounted under /order/manage/users/.
# ---------------------------------------------------------------------------

ALLOWED_USER_GROUPS = ('admin', 'staff')


def _require_admin(user):
    if not _is_admin(user):
        raise PermissionDenied


def _primary_group_name(user):
    g = user.groups.first()
    return g.name if g else ''


@login_required
def user_list(request):
    _require_admin(request.user)
    users = (
        User.objects.all()
        .prefetch_related('groups')
        .order_by('username')
    )
    rows = [{
        'user': u,
        'group': _primary_group_name(u),
        'is_self': u.pk == request.user.pk,
    } for u in users]
    return render(request, 'orders/manage/user_list.html', {
        'rows': rows,
    })


@login_required
def user_add(request):
    _require_admin(request.user)
    errors = {}
    form_data = {'username': '', 'group': 'staff'}

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        group_name = request.POST.get('group') or ''
        form_data = {'username': username, 'group': group_name}

        if not username:
            errors['username'] = 'กรุณากรอก username'
        elif User.objects.filter(username=username).exists():
            errors['username'] = 'username นี้ถูกใช้แล้ว'

        if not password:
            errors['password'] = 'กรุณากรอกรหัสผ่าน'
        elif len(password) < 4:
            errors['password'] = 'รหัสผ่านสั้นเกินไป (อย่างน้อย 4 ตัวอักษร)'

        if group_name not in ALLOWED_USER_GROUPS:
            errors['group'] = 'กรุณาเลือก group'

        if not errors:
            user = User.objects.create_user(username=username, password=password)
            user.groups.add(Group.objects.get(name=group_name))
            messages.success(request, f'เพิ่ม user "{username}" แล้ว')
            return redirect('user_list')

    return render(request, 'orders/manage/user_form.html', {
        'mode': 'add',
        'title': 'เพิ่ม user ใหม่',
        'form_data': form_data,
        'errors': errors,
        'allowed_groups': ALLOWED_USER_GROUPS,
    })


@login_required
def user_edit(request, pk):
    _require_admin(request.user)
    target = get_object_or_404(User, pk=pk)
    errors = {}
    form_data = {'group': _primary_group_name(target)}

    if request.method == 'POST':
        password = request.POST.get('password') or ''
        group_name = request.POST.get('group') or ''
        form_data = {'group': group_name}

        # Password optional on edit — only validate if provided.
        if password and len(password) < 4:
            errors['password'] = 'รหัสผ่านสั้นเกินไป (อย่างน้อย 4 ตัวอักษร)'

        if group_name not in ALLOWED_USER_GROUPS:
            errors['group'] = 'กรุณาเลือก group'

        if not errors:
            if password:
                target.set_password(password)
                target.save()
            target.groups.clear()
            target.groups.add(Group.objects.get(name=group_name))
            messages.success(request, f'อัปเดต user "{target.username}" แล้ว')
            return redirect('user_list')

    return render(request, 'orders/manage/user_form.html', {
        'mode': 'edit',
        'title': f'แก้ user: {target.username}',
        'target': target,
        'form_data': form_data,
        'errors': errors,
        'allowed_groups': ALLOWED_USER_GROUPS,
    })


@login_required
def user_delete(request, pk):
    _require_admin(request.user)
    target = get_object_or_404(User, pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, 'ไม่สามารถลบ user ของตัวเองได้')
        return redirect('user_list')

    if request.method == 'POST':
        username = target.username
        target.delete()
        messages.success(request, f'ลบ user "{username}" แล้ว')
        return redirect('user_list')

    return render(request, 'orders/manage/user_delete.html', {
        'target': target,
    })


# ---------------------------------------------------------------------------
# Custom search (admin-only) — filters: คนเย็บ + แหล่งที่มา + ผลิตที่ + ช่วงวันที่สร้าง.
# All filters AND together; every field is optional (blank = skip that one).
# Mounted at /order/search/.
# ---------------------------------------------------------------------------

@login_required
def custom_search(request):
    _require_admin(request.user)

    tailors = Tailor.objects.filter(is_active=True).order_by('name')

    tailor_id = (request.GET.get('tailor') or '').strip()
    source = (request.GET.get('source') or '').strip()
    production_place = (request.GET.get('production_place') or '').strip()
    date_from = (request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('date_to') or '').strip()

    selected_tailor = None
    if tailor_id:
        try:
            selected_tailor = Tailor.objects.get(pk=tailor_id)
        except (Tailor.DoesNotExist, ValueError):
            selected_tailor = None

    valid_source = source in dict(Order.SOURCE_CHOICES)
    valid_production = production_place in dict(Order.PRODUCTION_CHOICES)
    parsed_from = parse_date(date_from)
    parsed_to = parse_date(date_to)

    # Any usable filter present? Blank/invalid fields are simply ignored.
    has_filter = bool(selected_tailor or valid_source or valid_production or parsed_from or parsed_to)

    orders = None  # None = ยังไม่ได้กดค้นหา / ไม่มี filter
    if has_filter:
        qs = Order.objects.all()
        if selected_tailor is not None:
            qs = qs.filter(tailors=selected_tailor)
        if valid_source:
            qs = qs.filter(source=source)
        if valid_production:
            qs = qs.filter(production_place=production_place)
        if parsed_from:
            qs = qs.filter(created_date__gte=parsed_from)
        if parsed_to:
            qs = qs.filter(created_date__lte=parsed_to)
        orders = qs.order_by('-is_urgent', '-created_date', '-id').distinct()

    return render(request, 'orders/custom_search.html', {
        'tailors': tailors,
        'orders': orders,
        'selected_tailor': selected_tailor,
        'tailor_id': tailor_id,
        'source_choices': Order.SOURCE_CHOICES,
        'source': source,
        'production_choices': Order.PRODUCTION_CHOICES,
        'production_place': production_place,
        'date_from': date_from,
        'date_to': date_to,
        'has_filter': has_filter,
    })


# ---------------------------------------------------------------------------
# Customer profiles (เฟส 1 CRM): list / detail+edit / autocomplete API
# ---------------------------------------------------------------------------


def _filtered_customers(request):
    """Queryset ลูกค้าตาม filter ปัจจุบัน (?q= ค้นหา + ?tag=<id> กลุ่ม) —
    ใช้ร่วมกันระหว่างหน้ารายชื่อและ export CSV ให้ผลตรงกันเสมอ."""
    q = (request.GET.get('q') or '').strip()
    tag_id = (request.GET.get('tag') or '').strip()
    customers = (
        Customer.objects
        .annotate(order_count=Count('orders', distinct=True),
                  last_order=Max('orders__created_date'))
        .prefetch_related('prices', 'tags')
        .order_by('name')
    )
    if q:
        customers = customers.filter(
            Q(name__icontains=q) |
            Q(facebook_link__icontains=q) |
            Q(phone__icontains=q)
        )
    active_tag = None
    if tag_id.isdigit():
        active_tag = CustomerTag.objects.filter(pk=int(tag_id)).first()
        if active_tag:
            customers = customers.filter(tags=active_tag)
    return customers, q, active_tag


@login_required
def customer_list(request):
    """รายชื่อลูกค้าทั้งหมด + ค้นหา (ชื่อ/ลิงก์/เบอร์) + filter กลุ่ม (เฟส 4)
    + สรุปจำนวนใบต่อคน + ปุ่ม export CSV ตาม filter ปัจจุบัน."""
    customers, q, active_tag = _filtered_customers(request)
    all_tags = CustomerTag.objects.annotate(customer_count=Count('customers'))
    return render(request, 'orders/customer_list.html', {
        'customers': customers,
        'q': q,
        'active_tag': active_tag,
        'all_tags': all_tags,
    })


@login_required
def customer_export_csv(request):
    """Export รายชื่อลูกค้าตาม filter ปัจจุบัน (?q= + ?tag=) เป็น CSV
    เปิดใน Excel ได้ — ไว้ทำรายชื่อส่งข่าวส่วนลด/ของขวัญตามกลุ่ม.
    charset ต้องเป็น utf-8 + เขียน BOM เองครั้งเดียว (Lessons ข้อ 13 — ห้าม utf-8-sig)."""
    import csv

    customers, q, active_tag = _filtered_customers(request)
    filename = 'customers'
    if active_tag:
        filename += f'_{active_tag.name}'
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename)}.csv"
    resp.write('﻿')
    writer = csv.writer(resp)
    writer.writerow(['ชื่อลูกค้า', 'Facebook/ลิงก์', 'เบอร์โทร', 'กลุ่ม',
                     'จำนวนใบงาน', 'สั่งล่าสุด', 'โน้ต'])
    for c in customers:
        writer.writerow([
            c.name,
            c.facebook_link,
            c.phone,
            ', '.join(t.name for t in c.tags.all()),
            c.order_count,
            c.last_order.strftime('%Y-%m-%d') if c.last_order else '',
            c.note,
        ])
    return resp


def _save_customer_prices(request, customer):
    """Rebuild ตารางราคาของลูกค้าจาก parallel POST arrays
    (price_label[] / price_value[]) — wipe-and-recreate pattern เดียวกับ
    ExtraNameRow. แถวที่ label+ราคาว่างหมด หรือราคาไม่ใช่ตัวเลข → ข้าม."""
    labels = request.POST.getlist('price_label')
    values = request.POST.getlist('price_value')
    customer.prices.all().delete()
    rows = []
    for i in range(max(len(labels), len(values))):
        label = (labels[i] if i < len(labels) else '').strip()
        raw = (values[i] if i < len(values) else '').strip()
        if not label and not raw:
            continue
        try:
            price = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        rows.append(CustomerPrice(
            customer=customer,
            label=label or 'ราคาต่อตัว',
            price=price,
            order_index=i,
        ))
    if rows:
        CustomerPrice.objects.bulk_create(rows)


def _save_customer_tags(request, customer):
    """เซ็ตกลุ่มของลูกค้าจากฟอร์มโปรไฟล์ (เฟส 4): checkbox `tags` (id ที่ติ๊ก)
    + ช่อง `new_tags` (ชื่อกลุ่มใหม่ คั่น comma — get_or_create แล้วติ๊กให้เลย)."""
    tag_ids = [int(t) for t in request.POST.getlist('tags') if t.isdigit()]
    tags = list(CustomerTag.objects.filter(pk__in=tag_ids))
    new_names = (request.POST.get('new_tags') or '').split(',')
    for raw in new_names:
        tag_name = raw.strip()[:50]
        if not tag_name:
            continue
        tag, _created = CustomerTag.objects.get_or_create(name=tag_name)
        tags.append(tag)
    customer.tags.set(tags)


@login_required
def customer_detail(request, pk):
    """โปรไฟล์ลูกค้า: แก้ข้อมูล + ตารางราคา + กลุ่ม (tag) + ประวัติใบงานทั้งหมดของคนนั้น."""
    customer = get_object_or_404(Customer, pk=pk)

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if name:
            customer.name = name
            customer.facebook_link = (request.POST.get('facebook_link') or '').strip()
            customer.phone = (request.POST.get('phone') or '').strip()
            customer.note = (request.POST.get('note') or '').strip()
            customer.save()
            _save_customer_prices(request, customer)
            _save_customer_tags(request, customer)
            messages.success(request, 'บันทึกข้อมูลลูกค้าแล้ว')
            return redirect('customer_detail', pk=customer.pk)
        messages.error(request, 'กรุณาระบุชื่อลูกค้า')

    orders = (
        customer.orders
        .prefetch_related('items', 'items__variants')
        .order_by('-created_date', '-id')
    )
    return render(request, 'orders/customer_detail.html', {
        'customer': customer,
        'orders': orders,
        'prices': list(customer.prices.all()),
        'all_tags': CustomerTag.objects.all(),
        'customer_tags': list(customer.tags.all()),
    })


@login_required
def customer_search_api(request):
    """Autocomplete ในฟอร์มใบงาน: ?q=... → JSON ลูกค้า 10 คนแรกที่ match
    (ชื่อ/ลิงก์/เบอร์) พร้อมตารางราคาของแต่ละคน (ใช้ทำปุ่มคำนวณยอดรวม)."""
    q = (request.GET.get('q') or '').strip()
    results = []
    if q:
        customers = (
            Customer.objects
            .filter(Q(name__icontains=q) |
                    Q(facebook_link__icontains=q) |
                    Q(phone__icontains=q))
            .prefetch_related('prices')
            .order_by('name')[:10]
        )
        for c in customers:
            results.append({
                'id': c.pk,
                'name': c.name,
                'facebook_link': c.facebook_link,
                'phone': c.phone,
                'prices': [
                    {'label': p.label, 'price': float(p.price)}
                    for p in c.prices.all()
                ],
            })
    return JsonResponse({'results': results})


@login_required
@require_POST
def customer_create(request):
    """สร้างโปรไฟล์ลูกค้ามือ (ปุ่มบนหน้ารายชื่อ) — กรอกแค่ชื่อ แล้วพาไป
    หน้าโปรไฟล์เพื่อเติมลิงก์/เบอร์/ราคา. ปกติโปรไฟล์เกิดอัตโนมัติจากใบงาน
    — ปุ่มนี้ไว้เคสตั้งราคาล่วงหน้าก่อนมีใบแรก."""
    name = (request.POST.get('name') or '').strip()
    if not name:
        messages.error(request, 'กรุณาระบุชื่อลูกค้า')
        return redirect('customer_list')
    customer = Customer.objects.create(name=name)
    return redirect('customer_detail', pk=customer.pk)


@login_required
def brief_jobs_api(request):
    """Proxy autocomplete "เลขใบงานออกแบบ" → internal API ฝั่ง Brief (เฟส 3).
    เรียกฝั่ง server (localhost) เพื่อไม่ให้ token หลุดไป browser; Brief
    ล่ม/ไม่ได้ตั้ง token = คืน results ว่าง ฟอร์มใช้งานต่อได้ปกติ."""
    import urllib.parse
    import urllib.request

    q = (request.GET.get('q') or '').strip()
    if not (settings.BRIEF_API_TOKEN or settings.DEBUG):
        return JsonResponse({'results': [], 'disabled': True})
    url = f'{settings.BRIEF_API_BASE}/api/jobs/?q={urllib.parse.quote(q)}'
    headers = {}
    if settings.BRIEF_API_TOKEN:
        headers['X-Api-Token'] = settings.BRIEF_API_TOKEN
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except (OSError, ValueError):
        return JsonResponse({'results': [], 'error': 'brief_unreachable'})
    return JsonResponse({'results': payload.get('results', [])})
