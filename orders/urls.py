from django.urls import path
from . import views

urlpatterns = [
    path('', views.order_list, name='order_list'),
    path('create/', views.order_create, name='order_create'),
    path('daily-summary/', views.daily_summary, name='daily_summary'),
    path('reports/', views.reports, name='reports'),
    path('search/', views.custom_search, name='custom_search'),

    # Production-channel (cookie-based, public — see CLAUDE-V1.6.md §1)
    path('select-department/', views.select_department, name='select_department'),
    path('clear-department/', views.clear_department, name='clear_department'),
    path('dept/<slug:slug>/', views.dept_dashboard, name='dept_dashboard'),
    path('<str:order_number>/update/', views.update_order_stage, name='update_order_stage'),

    # User management (admin-only)
    path('manage/users/', views.user_list, name='user_list'),
    path('manage/users/add/', views.user_add, name='user_add'),
    path('manage/users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('manage/users/<int:pk>/delete/', views.user_delete, name='user_delete'),

    path('<int:pk>/', views.order_detail, name='order_detail'),
    path('<int:pk>/edit/', views.order_edit, name='order_edit'),
    path('<int:pk>/print/', views.order_print, name='order_print'),
    path('<int:pk>/extra-csv/', views.order_extra_csv, name='order_extra_csv'),
    path('<int:pk>/pick/', views.order_pick, name='order_pick'),
    path('<int:pk>/master/', views.order_master, name='order_master'),
    path('<int:pk>/mark-printed/', views.order_mark_printed, name='order_mark_printed'),
    path('<int:pk>/delete/', views.order_delete, name='order_delete'),
]
