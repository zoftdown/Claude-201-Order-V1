from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer_name', 'shirt_name', 'source', 'status', 'created_date']
    list_filter = ['status', 'source']
    search_fields = ['order_number', 'customer_name', 'shirt_name']
    readonly_fields = ['order_number']
    inlines = [OrderItemInline]
