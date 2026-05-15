from django.urls import path
from . import views

urlpatterns = [
    path('', views.liste_clients, name='clients'),
    path('detail/<int:id>/', views.detail_client, name='detail_client'),
    path('rapport-financier/<int:id>/', views.rapport_financier_client, name='rapport_financier_client'),
    path('commandes-infos/', views.commandes_client_infos, name='commandes_client_infos'),
    path('banques/ajouter-modal/', views.ajouter_banque_modal, name='ajouter_banque_modal'),
    path('encaissements/', views.encaissements_clients, name='encaissements_clients'),
    path('encaissements/historique/', views.historique_encaissements_clients, name='historique_encaissements_clients'),
    path('encaissements/imputer-avance/<int:id>/', views.imputer_avance_client, name='imputer_avance_client'),
    path('encaissements/modifier/<int:id>/', views.modifier_encaissement_client, name='modifier_encaissement_client'),
    path('encaissements/supprimer/<int:id>/', views.supprimer_encaissement_client, name='supprimer_encaissement_client'),
    path('portefeuilles/', views.portefeuille_clients, name='portefeuille_clients'),
    path('ajouter/', views.ajouter_client, name='ajouter_client'),
    path('modifier/<int:id>/', views.modifier_client, name='modifier_client'),
    path('supprimer/<int:id>/', views.supprimer_client, name='supprimer_client'),
    path('ajouter-modal/', views.ajouter_client_modal, name='ajouter_client_modal'),
    path('prospect-infos/', views.prospect_infos, name='prospect_infos'),
]
