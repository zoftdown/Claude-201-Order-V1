from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.utils import timezone
from .models import Order
from .forms import OrderForm, OrderItemFormSet


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
            return redirect('order_detail', pk=order.pk)
    else:
        form = OrderForm()
        formset = OrderItemFormSet()

    return render(request, 'orders/order_form.html', {
        'form': form,
        'formset': formset,
        'title': 'สร้างออร์เดอร์ใหม่',
    })


def order_edit(request, pk):
    order = get_object_or_404(Order, pk=pk)
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        formset = OrderItemFormSet(request.POST, request.FILES, instance=order)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
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


def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    return render(request, 'orders/order_detail.html', {'order': order})


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
