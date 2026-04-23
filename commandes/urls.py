from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_commandes, name="commandes"),
    path("ajouter/", views.ajouter_commande, name="ajouter_commande"),
    path("modifier/<int:id>/", views.modifier_commande, name="modifier_commande"),
    path("supprimer/<int:id>/", views.supprimer_commande, name="supprimer_commande"),
    path("apercu-dg/<int:id>/", views.apercu_commande_dg, name="apercu_commande_dg"),
    path("valider-dg/<int:id>/", views.valider_commande_dg, name="valider_commande_dg"),
    path("rejeter-dg/<int:id>/", views.rejeter_commande_dg, name="rejeter_commande_dg"),
    path("affecter-logistique/<int:id>/", views.affecter_commande_logistique, name="affecter_commande_logistique"),
    path("completer-capacite/<int:id>/", views.completer_capacite_commande, name="completer_capacite_commande"),
    path("camion-infos/", views.commande_camion_infos, name="commande_camion_infos"),
    path("export/xls/", views.export_commandes_xls, name="export_commandes_xls"),
    path("export/pdf/", views.export_commandes_pdf, name="export_commandes_pdf"),
]
