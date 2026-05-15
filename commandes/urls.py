from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_commandes, name="commandes"),
    path("rapport-global/", views.rapport_global, name="rapport_global"),
    path("rapport-global/<int:id>/", views.detail_rapport_global, name="detail_rapport_global"),
    path("rapport-global/export/xls/", views.export_rapport_global_xls, name="export_rapport_global_xls"),
    path("rapport-global/export/pdf/", views.export_rapport_global_pdf, name="export_rapport_global_pdf"),
    path("detail/<int:id>/", views.detail_commande, name="detail_commande"),
    path("ajouter/", views.ajouter_commande, name="ajouter_commande"),
    path("modifier/<int:id>/", views.modifier_commande, name="modifier_commande"),
    path("numero/<int:id>/", views.renseigner_numero_commande, name="renseigner_numero_commande"),
    path("supprimer/<int:id>/", views.supprimer_commande, name="supprimer_commande"),
    path("apercu-dga/<int:id>/", views.apercu_commande_dga, name="apercu_commande_dga"),
    path("valider-dga/<int:id>/", views.valider_commande_dga, name="valider_commande_dga"),
    path("rejeter-dga/<int:id>/", views.rejeter_commande_dga, name="rejeter_commande_dga"),
    path("apercu-dg/<int:id>/", views.apercu_commande_dg, name="apercu_commande_dg"),
    path("valider-dg/<int:id>/", views.valider_commande_dg, name="valider_commande_dg"),
    path("rejeter-dg/<int:id>/", views.rejeter_commande_dg, name="rejeter_commande_dg"),
    path("affecter-logistique/<int:id>/", views.affecter_commande_logistique, name="affecter_commande_logistique"),
    path("completer-capacite/<int:id>/", views.completer_capacite_commande, name="completer_capacite_commande"),
    path("camion-infos/", views.commande_camion_infos, name="commande_camion_infos"),
    path("client-infos/", views.commande_client_infos, name="commande_client_infos"),
    path("affretes/ajouter-modal/", views.ajouter_affrete_modal, name="ajouter_affrete_modal"),
    path("affretes/ajouter-camion-modal/", views.ajouter_camion_affrete_existant_modal, name="ajouter_camion_affrete_existant_modal"),
    path("export/xls/", views.export_commandes_xls, name="export_commandes_xls"),
    path("export/pdf/", views.export_commandes_pdf, name="export_commandes_pdf"),
]
