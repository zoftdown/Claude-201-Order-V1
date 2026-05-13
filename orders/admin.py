from django.contrib import admin
from .models import Order, OrderItem, ShirtVariant, Tailor, StageLog, DepartmentPIN


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ['order_index', 'design_image']


class ShirtVariantInline(admin.TabularInline):
    model = ShirtVariant
    extra = 1
    fields = ['order_index', 'collar', 'sleeve', 'color', 'sizes', 'note']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'customer_name', 'shirt_name', 'source', 'status', 'created_date']
    list_filter = ['status', 'source']
    search_fields = ['order_number', 'customer_name', 'shirt_name']
    readonly_fields = ['order_number']
    inlines = [OrderItemInline]
    filter_horizontal = ['tailors']


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'order_index', 'design_image']
    list_filter = ['order']
    inlines = [ShirtVariantInline]


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


@admin.register(DepartmentPIN)
class DepartmentPINAdmin(admin.ModelAdmin):
    """Singleton model — keep one row, change the pin field to rotate."""
    list_display = ['pin', 'updated_at']
    readonly_fields = ['updated_at']

    def has_add_permission(self, request):
        # Only allow one row; reject add if a row already exists.
        return not DepartmentPIN.objects.exists()
