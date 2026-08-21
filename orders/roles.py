"""Role helper กลาง — logic เดียวใช้ทั้งระบบ (views + template filter).

ระบบมี 2 group: 'admin' กับ 'staff' (ดู create_default_groups + ALLOWED_USER_GROUPS
ใน views.py). "admin" = superuser หรืออยู่ใน group ชื่อ admin — นิยามเดิมที่เคย
เขียนซ้ำอยู่ทั้งใน views._is_admin และ templatetags/auth_extras.is_admin
ถูกยุบมารวมที่นี่ที่เดียว.
"""


def is_admin(user):
    """True เมื่อ user เป็น admin ของระบบ (superuser หรือ group 'admin').

    viewer จากแผนกผลิต (cookie, ไม่ login) เป็น AnonymousUser →
    is_authenticated=False → False เสมอ.
    """
    return bool(
        user
        and user.is_authenticated
        and (user.is_superuser or user.groups.filter(name='admin').exists())
    )
