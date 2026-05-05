from django import template

register = template.Library()


@register.filter(name='has_group')
def has_group(user, group_name):
    if not user.is_authenticated:
        return False
    return user.groups.filter(name=group_name).exists()


@register.filter(name='is_admin')
def is_admin(user):
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name='admin').exists()
