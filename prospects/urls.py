from django.urls import path
from . import views

urlpatterns = [

    path('', views.liste_prospects, name='prospects'),

    path('ajouter/', views.ajouter_prospect, name='ajouter_prospect'),
    path('modifier/<int:id>/', views.modifier_prospect, name='modifier_prospect'),
    path('supprimer/<int:id>/', views.supprimer_prospect, name='supprimer_prospect'),

    path('convertir/<int:id>/', views.convertir_client, name='convertir_client'),

]
