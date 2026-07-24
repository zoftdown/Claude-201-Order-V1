from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from orders.views import pin_login

urlpatterns = [
    path('admin/', admin.site.urls),
    # login หลัก = PIN ประจำตัว (ช่องเดียว); classic = username/password เดิม (fallback)
    path('login/', pin_login, name='login'),
    path('login/classic/', auth_views.LoginView.as_view(
        template_name='registration/login_classic.html'), name='login_classic'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('orders.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
