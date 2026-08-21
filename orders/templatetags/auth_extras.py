from django import template

from ..roles import is_admin as _is_admin

register = template.Library()


@register.filter(name='has_group')
def has_group(user, group_name):
    if not user.is_authenticated:
        return False
    return user.groups.filter(name=group_name).exists()


@register.filter(name='is_admin')
def is_admin(user):
    # logic จริงอยู่ orders/roles.py — ที่เดียวใช้ร่วมกับ views (_is_admin)
    return _is_admin(user)
