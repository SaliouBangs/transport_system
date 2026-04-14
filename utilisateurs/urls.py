from django.urls import path

from . import views


urlpatterns = [
    path("connexion/", views.connexion_view, name="connexion"),
    path("deconnexion/", views.deconnexion_view, name="deconnexion"),
    path("utilisateurs/", views.liste_utilisateurs, name="utilisateurs"),
    path("utilisateurs/ajouter/", views.ajouter_utilisateur, name="ajouter_utilisateur"),
    path("utilisateurs/modifier/<int:id>/", views.modifier_utilisateur, name="modifier_utilisateur"),
]
