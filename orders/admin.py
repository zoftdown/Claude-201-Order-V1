from django.contrib import admin
from .models import (
    Order, OrderItem, MasterImage, ShirtVariant, Tailor, StageLog,
    DepartmentPIN, Customer, CustomerPrice, CustomerTag,
    ShirtCost, DailyAdSpend, DailySummary,
)
from .profit import invalidate_all, invalidate_days


@admin.register(ShirtCost)
class ShirtCostAdmin(admin.ModelAdmin):
    """ต้นทุนเสื้อต่อตัว (แขนสั้น/แขนยาว/โปโล) — แก้ตัวเลขได้จากหน้า list เลย.
    แก้ต้นทุนกระทบกำไรทุกวันย้อนหลัง → ล้าง cache DailySummary ทั้งหมด."""
    list_display = ['shirt_type', 'cost']
    list_editable = ['cost']

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_all()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        invalidate_all()


@admin.register(DailyAdSpend)
class DailyAdSpendAdmin(admin.ModelAdmin):
    """ค่าแอดรายวันต่อเพจ — ปกติกรอกจากฟอร์มใน tab กำไรรายวัน (/reports/),
    หน้านี้ไว้แก้/ลบย้อนหลัง. แก้แล้วล้าง cache ของวันนั้นให้คำนวณใหม่."""
    list_display = ['date', 'page', 'amount']
    list_filter = ['page']
    date_hierarchy = 'date'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_days(obj.date)

    def delete_model(self, request, obj):
        day = obj.date
        super().delete_model(request, obj)
        invalidate_days(day)


@admin.register(DailySummary)
class DailySummaryAdmin(admin.ModelAdmin):
    """Cache สรุปกำไรรายวัน — read-only (ระบบเขียนเอง). ลบแถวได้ = บังคับ
    คำนวณวันนั้นใหม่รอบหน้า."""
    list_display = ['date', 'page', 'orders', 'shirts', 'revenue',
                    'shirt_cost', 'ad_spend', 'gross_profit', 'net_after_ads']
    list_filter = ['page']
    date_hierarchy = 'date'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CustomerTag)
class CustomerTagAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']


class CustomerPriceInline(admin.TabularInline):
    model = CustomerPrice
    extra = 1
    fields = ['order_index', 'label', 'price']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['name', 'facebook_link', 'phone', 'created_at']
    search_fields = ['name', 'facebook_link', 'phone']
    list_filter = ['tags']
    filter_horizontal = ['tags']
    inlines = [CustomerPriceInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ['order_index', 'design_image', 'shirt_type']


class MasterImageInline(admin.TabularInline):
    model = MasterImage
    extra = 1
    fields = ['order_index', 'image']


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
    inlines = [OrderItemInline, MasterImageInline]
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
