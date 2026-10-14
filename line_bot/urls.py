from django.urls import path

from . import views

urlpatterns = [
    # URL จริงบน prod = https://dr89.cloud/order/linebot/webhook/ (FORCE_SCRIPT_NAME=/order)
    path('webhook/', views.webhook, name='linebot_webhook'),
]
