from django.urls import path
from . import views

urlpatterns = [
    path('', views.liste_clients, name='clients'),
    path('ajouter/', views.ajouter_client, name='ajouter_client'),
    path('modifier/<int:id>/', views.modifier_client, name='modifier_client'),
    path('supprimer/<int:id>/', views.supprimer_client, name='supprimer_client'),
    path('ajouter-modal/', views.ajouter_client_modal, name='ajouter_client_modal'),
    path('prospect-infos/', views.prospect_infos, name='prospect_infos'),
]
