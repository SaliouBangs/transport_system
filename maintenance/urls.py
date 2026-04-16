from django.urls import path

from . import views


urlpatterns = [
    path("", views.garage_maintenances, name="maintenances"),
    path("garage/", views.garage_maintenances, name="garage_maintenances"),
    path("garage/ajouter/", views.ajouter_maintenance_garage, name="ajouter_maintenance_garage"),
    path("garage/modifier/<int:id>/", views.modifier_maintenance_garage, name="modifier_maintenance_garage"),
    path("garage/terminer/<int:id>/", views.terminer_maintenance, name="terminer_maintenance"),
    path("garage/valider-logistique/<int:id>/", views.valider_maintenance_logistique, name="valider_maintenance_logistique"),
    path("garage/rejeter-dga/<int:id>/", views.rejeter_maintenance_dga, name="rejeter_maintenance_dga"),
    path("garage/valider-dga/<int:id>/", views.valider_maintenance_dga, name="valider_maintenance_dga"),
    path("garage/rejeter-dg/<int:id>/", views.rejeter_maintenance_dg, name="rejeter_maintenance_dg"),
    path("garage/valider-dg/<int:id>/", views.valider_maintenance_dg, name="valider_maintenance_dg"),
    path("garage/apercu-validation/<int:id>/", views.apercu_validation_maintenance, name="apercu_validation_maintenance"),
    path("garage/imprimer/<int:id>/", views.imprimer_maintenance, name="imprimer_maintenance"),
    path("garage/export/xls/", views.export_garage_xls, name="export_garage_xls"),
    path("garage/export/pdf/", views.export_garage_pdf, name="export_garage_pdf"),
    path("achat/", views.achat_maintenances, name="achat_maintenances"),
    path("achat/modifier/<int:id>/", views.modifier_maintenance_achat, name="modifier_maintenance_achat"),
    path("achat/export/xls/", views.export_achat_xls, name="export_achat_xls"),
    path("achat/export/pdf/", views.export_achat_pdf, name="export_achat_pdf"),
    path("paiements/", views.paiements_maintenances, name="paiements_maintenances"),
    path("paiements/modifier/<int:id>/", views.modifier_maintenance_paiement, name="modifier_maintenance_paiement"),
    path("fournisseurs/", views.fournisseurs_maintenance, name="fournisseurs_maintenance"),
    path("fournisseurs/ajouter/", views.ajouter_fournisseur, name="ajouter_fournisseur"),
    path("fournisseurs/modifier/<int:id>/", views.modifier_fournisseur, name="modifier_fournisseur"),
    path("fournisseurs/supprimer/<int:id>/", views.supprimer_fournisseur, name="supprimer_fournisseur"),
    path("supprimer/<int:id>/", views.supprimer_maintenance, name="supprimer_maintenance"),
    path(
        "types/ajouter-modal/",
        views.ajouter_type_maintenance_modal,
        name="ajouter_type_maintenance_modal",
    ),
    path(
        "fournisseurs/ajouter-modal/",
        views.ajouter_fournisseur_modal,
        name="ajouter_fournisseur_modal",
    ),
    path(
        "prestataires/ajouter-modal/",
        views.ajouter_prestataire_modal,
        name="ajouter_prestataire_modal",
    ),
]
