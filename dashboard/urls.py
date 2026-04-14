from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('gps/', views.gps_monitor, name='gps_monitor'),
]
