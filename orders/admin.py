from django.contrib import admin
from .models import Order, OrderItem, Tailor, StageLog


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
    filter_horizontal = ['tailors']


@admin.register(Tailor)
class TailorAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'phone']


@admin.register(StageLog)
class StageLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'order', 'department', 'action']
    list_filter = ['department', 'action']
    search_fields = ['order__order_number']
    readonly_fields = ['order', 'department', 'action', 'note', 'created_at']
    date_hierarchy = 'created_at'
