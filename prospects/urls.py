from django.urls import path
from . import views

urlpatterns = [

    path('', views.liste_prospects, name='prospects'),

    path('ajouter/', views.ajouter_prospect, name='ajouter_prospect'),

    path('convertir/<int:id>/', views.convertir_client, name='convertir_client'),

]
