from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_operations, name="operations"),
    path("export/xls/", views.export_operations_xls, name="export_operations_xls"),
    path("export/pdf/", views.export_operations_pdf, name="export_operations_pdf"),
    path("ajouter/", views.ajouter_operation, name="ajouter_operation"),
    path("modifier/<int:id>/", views.modifier_operation, name="modifier_operation"),
    path("supprimer/<int:id>/", views.supprimer_operation, name="supprimer_operation"),
    path("comptable/", views.comptable_operations, name="comptable_operations"),
    path("comptable/sommiers/", views.sommiers_operations, name="sommiers_operations"),
    path("comptable/ajouter/", views.ajouter_operation_comptable, name="ajouter_operation_comptable"),
    path("comptable/<int:id>/modifier/", views.modifier_operation_comptable, name="modifier_operation_comptable"),
    path("comptable/<int:id>/bon-livraison/", views.imprimer_bon_livraison, name="imprimer_bon_livraison"),
    path("facturation/", views.facturation_operations, name="facturation_operations"),
    path("facturation/<int:id>/modifier/", views.modifier_operation_facturation, name="modifier_operation_facturation"),
    path("facturation/<int:id>/sans-tva/", views.imprimer_facture_sans_tva, name="imprimer_facture_sans_tva"),
    path("facturation/<int:id>/avec-tva/", views.imprimer_facture_avec_tva, name="imprimer_facture_avec_tva"),
    path("logistique/", views.logistique_operations, name="logistique_operations"),
    path("logistique/<int:id>/modifier/", views.modifier_operation_logistique, name="modifier_operation_logistique"),
    path("logistique/historique/<int:id>/", views.ancienne_fiche_operation_logistique, name="ancienne_fiche_operation_logistique"),
    path("transitaire/", views.transitaire_operations, name="transitaire_operations"),
    path("transitaire/<int:id>/<str:etat>/", views.changer_etat_transitaire, name="changer_etat_transitaire"),
    path("logisticien/", views.logisticien_operations, name="logisticien_operations"),
    path("logisticien/<int:id>/modifier/", views.modifier_operation_logisticien, name="modifier_operation_logisticien"),
    path("produits/ajouter-modal/", views.ajouter_produit_modal, name="ajouter_produit_modal"),
    path("regimes/ajouter-modal/", views.ajouter_regime_modal, name="ajouter_regime_modal"),
    path("depots/ajouter-modal/", views.ajouter_depot_modal, name="ajouter_depot_modal"),
    path("chauffeur-par-camion/", views.chauffeur_par_camion, name="chauffeur_par_camion"),
    path("commande-infos/", views.commande_infos, name="commande_infos"),
]
