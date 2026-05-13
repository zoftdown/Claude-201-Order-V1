"""
Decorators for production-channel access (cookie-based, no Django login).

See CLAUDE-V1.6.md §1 — production users identify themselves once via the
'production_dept' cookie, then act anonymously thereafter.

Phase 1.8 adds a 4-digit PIN gate. After a successful PIN entry the user
also receives a 'production_pin_hash' cookie whose value is the sha256 of
the current DB PIN. Decorators reject cookies whose hash doesn't match the
current PIN — so rotating the PIN in /admin/ instantly logs out every
existing device.
"""

from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

from .departments import VALID_SLUGS, VIEWER_SLUG, get_department

DEPT_COOKIE_NAME = 'production_dept'
DEPT_PIN_HASH_COOKIE = 'production_pin_hash'


def _redirect_to_select(request, reason=None):
    target = reverse('select_department')
    qs = {'next': request.get_full_path()}
    if reason:
        qs['reason'] = reason
    return redirect(f'{target}?{urlencode(qs)}')


def _pin_hash_ok(request):
    """True if the cookie's pin_hash equals the current DB PIN's hash."""
    # Lazy import: decorators is imported by views which imports forms, so
    # keep the model import inside the call to avoid an import cycle at
    # module load.
    from .models import DepartmentPIN
    return request.COOKIES.get(DEPT_PIN_HASH_COOKIE) == DepartmentPIN.current_hash()


def require_department(view_func):
    """Redirect to /select-department/?next=... unless a valid dept cookie is set
    AND the cookie's pin_hash matches the current DB PIN.

    On success, attaches request.production_dept (the dept dict) for the view.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        slug = request.COOKIES.get(DEPT_COOKIE_NAME)
        dept = get_department(slug) if slug in VALID_SLUGS else None
        if dept is None:
            return _redirect_to_select(request)
        if not _pin_hash_ok(request):
            return _redirect_to_select(request, reason='pin_expired')
        request.production_dept = dept
        return view_func(request, *args, **kwargs)

    return _wrapped


def viewer_or_login_required(view_func):
    """Read-only views (list / detail / print) accept either path:

    - Logged-in Django user        → request.is_viewer = False
    - ANY valid production_dept    → request.is_viewer = True
      cookie + valid pin_hash
    - Otherwise                    → redirect to /login/?next=...

    Production-dept users land here when they click "ดูใบ" from the
    dashboard search results. Treating them as is_viewer hides prices
    and edit/delete buttons — same UX as the read-only viewer dept.
    """

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.user.is_authenticated:
            request.is_viewer = False
            return view_func(request, *args, **kwargs)
        slug = request.COOKIES.get(DEPT_COOKIE_NAME)
        if slug in VALID_SLUGS:
            if not _pin_hash_ok(request):
                return _redirect_to_select(request, reason='pin_expired')
            request.is_viewer = True
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path())

    return _wrapped
