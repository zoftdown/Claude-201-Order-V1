from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .decorators import require_department
from .departments import DEPARTMENTS, VALID_SLUGS
from .forms import OrderForm, OrderItemFormSet
from .models import Order

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


@login_required
def order_create(request):
    if request.method == 'POST':
        form = OrderForm(request.POST)
        formset = OrderItemFormSet(request.POST, request.FILES)
        if form.is_valid() and formset.is_valid():
            order = form.save(commit=False)
            order.created_date = timezone.now().date()
            order.save()
            formset.instance = order
            formset.save()
            _copy_images_from_first(request, formset)
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm()
        formset = OrderItemFormSet()

    return render(request, 'orders/order_form.html', {
        'form': form,
        'formset': formset,
        'title': 'สร้างออร์เดอร์ใหม่',
    })


@login_required
def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        formset = OrderItemFormSet(request.POST, request.FILES, instance=order)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            _copy_images_from_first(request, formset)
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm(instance=order)
        formset = OrderItemFormSet(instance=order)

    return render(request, 'orders/order_form.html', {
        'form': form,
        'formset': formset,
        'order': order,
        'title': f'แก้ไขออร์เดอร์ {order.order_number}',
    })


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

    return render(request, 'orders/order_print.html', {
        'order': order,
        'pages': pages,
        'is_single_item': len(items) == 1,
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


@require_department
def dept_dashboard(request, slug):
    # Step 4 will replace this with the real dashboard.
    # For now, prove the cookie + decorator round-trip works end-to-end.
    return render(request, 'orders/dept_placeholder.html', {
        'dept': request.production_dept,
        'requested_slug': slug,
    })
