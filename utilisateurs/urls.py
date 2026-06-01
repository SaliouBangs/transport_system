from django.urls import path

from . import views


urlpatterns = [
    path("connexion/", views.connexion_view, name="connexion"),
    path("acces-technique/", views.acces_technique_view, name="acces_technique"),
    path("deconnexion/", views.deconnexion_view, name="deconnexion"),
    path("entite-supervision/", views.changer_entite_supervision, name="changer_entite_supervision"),
    path("notifications/statut/", views.notifications_status, name="notifications_status"),
    path("messages/", views.messages_internes_view, name="messages_internes"),
    path("profil/", views.profil_view, name="profil"),
    path("parametres/", views.parametres_view, name="parametres"),
    path("actions/", views.historique_actions_view, name="historique_actions"),
    path("utilisateurs/", views.liste_utilisateurs, name="utilisateurs"),
    path("utilisateurs/ajouter/", views.ajouter_utilisateur, name="ajouter_utilisateur"),
    path("utilisateurs/modifier/<int:id>/", views.modifier_utilisateur, name="modifier_utilisateur"),
]
