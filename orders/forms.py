import json
from django import forms
from django.forms import inlineformset_factory
from .models import Order, OrderItem


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
            'total_price', 'deposit', 'delivery_method', 'status',
        ]
        widgets = {
            'fabric_spec': forms.Textarea(attrs={'rows': 2, 'placeholder': 'ผ้า 120 แกรม'}),
            'special_note': forms.Textarea(attrs={'rows': 2}),
        }


class OrderItemForm(BootstrapMixin, forms.ModelForm):
    sizes_json = forms.CharField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = OrderItem
        fields = ['design_image', 'shirt_type', 'color']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.sizes:
            self.fields['sizes_json'].initial = json.dumps(self.instance.sizes, ensure_ascii=False)
        else:
            default = [{'label': s, 'qty': 0} for s in OrderItem.DEFAULT_SIZES]
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
