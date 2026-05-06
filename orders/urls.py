from django.urls import path
from . import views

urlpatterns = [
    path('', views.order_list, name='order_list'),
    path('create/', views.order_create, name='order_create'),

    # Production-channel (cookie-based, public — see CLAUDE-V1.6.md §1)
    path('select-department/', views.select_department, name='select_department'),
    path('clear-department/', views.clear_department, name='clear_department'),
    path('dept/<slug:slug>/', views.dept_dashboard, name='dept_dashboard'),
    path('<str:order_number>/update/', views.update_order_stage, name='update_order_stage'),

    path('<int:pk>/', views.order_detail, name='order_detail'),
    path('<int:pk>/edit/', views.order_edit, name='order_edit'),
    path('<int:pk>/print/', views.order_print, name='order_print'),
    path('<int:pk>/delete/', views.order_delete, name='order_delete'),
]
