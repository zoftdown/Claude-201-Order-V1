import json
from django import forms
from django.forms import inlineformset_factory
from .models import Order, OrderItem, ShirtVariant


# Default sizes shown when a new variant is added.
DEFAULT_SIZES = ['S', 'M', 'L', 'XL', '2XL', '3XL', '4XL']

# Suggestion lists rendered as <datalist>. User can pick or type freely.
COLLAR_SUGGESTIONS = ['คอกลม', 'คอวี', 'โปโล', 'คอปกวี', 'คอกีฬา']
SLEEVE_SUGGESTIONS = ['แขนสั้น', 'แขนยาว', 'แขนกุด']


class BootstrapMixin:
    """Auto-apply Bootstrap classes to all form fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.Select):
                widget.attrs.setdefault('class', 'form-select form-select-sm')
            elif isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif isinstance(widget, forms.FileInput):
                widget.attrs.setdefault('class', 'form-control form-control-sm')
            else:
                widget.attrs.setdefault('class', 'form-control form-control-sm')


class OrderForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Order
        fields = [
            'source', 'customer_name',
            'customer_link', 'shirt_name', 'fabric_spec', 'special_note',
            'total_price', 'deposit', 'delivery_method', 'shipping_address', 'status',
        ]
        widgets = {
            'fabric_spec': forms.Textarea(attrs={'rows': 2, 'placeholder': 'ผ้า 120 แกรม'}),
            'special_note': forms.Textarea(attrs={'rows': 2}),
            'shipping_address': forms.Textarea(attrs={'rows': 3, 'placeholder': 'ชื่อ-ที่อยู่-เบอร์โทร สำหรับจัดส่งพัสดุ'}),
        }


class OrderItemForm(BootstrapMixin, forms.ModelForm):
    """One design image + ordering. Variant fields live on ShirtVariantForm."""

    class Meta:
        model = OrderItem
        fields = ['design_image']


class ShirtVariantForm(BootstrapMixin, forms.ModelForm):
    """One "แบบ" — collar/sleeve/color/sizes/note for a given design image."""

    sizes_json = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = ShirtVariant
        fields = ['collar', 'sleeve', 'color', 'note']
        widgets = {
            'collar': forms.TextInput(attrs={'list': 'collar-suggestions', 'placeholder': 'คอกลม'}),
            'sleeve': forms.TextInput(attrs={'list': 'sleeve-suggestions', 'placeholder': 'แขนสั้น'}),
            'color': forms.TextInput(attrs={'placeholder': 'สี'}),
            'note': forms.TextInput(attrs={'placeholder': 'โน้ต (ถ้ามี)'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Seed sizes_json with current sizes (edit) or default labels (new).
        if self.instance and self.instance.pk and self.instance.sizes:
            self.fields['sizes_json'].initial = json.dumps(self.instance.sizes, ensure_ascii=False)
        else:
            default = [{'label': s, 'qty': 0} for s in DEFAULT_SIZES]
            self.fields['sizes_json'].initial = json.dumps(default, ensure_ascii=False)

    def save(self, commit=True):
        instance = super().save(commit=False)
        raw = self.cleaned_data.get('sizes_json', '[]')
        try:
            sizes = json.loads(raw)
            instance.sizes = [s for s in sizes if s.get('label', '').strip()]
        except (json.JSONDecodeError, TypeError):
            instance.sizes = []
        if commit:
            instance.save()
        return instance


OrderItemFormSet = inlineformset_factory(
    Order,
    OrderItem,
    form=OrderItemForm,
    extra=1,
    can_delete=True,
)


# Inner formset: variants under an OrderItem.
ShirtVariantFormSet = inlineformset_factory(
    OrderItem,
    ShirtVariant,
    form=ShirtVariantForm,
    extra=1,
    can_delete=True,
)
