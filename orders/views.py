from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Order
from .forms import OrderForm, OrderItemFormSet


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
