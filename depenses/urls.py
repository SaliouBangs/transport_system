from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_depenses, name="liste_depenses"),
    path("types/", views.liste_types_depense, name="liste_types_depense"),
    path("types/<int:id>/modifier/", views.modifier_type_depense, name="modifier_type_depense"),
    path("types/<int:id>/supprimer/", views.supprimer_type_depense, name="supprimer_type_depense"),
    path("ajouter/", views.ajouter_depense, name="ajouter_depense"),
    path("chargement/<int:operation_id>/ajouter/", views.ajouter_depense_chargement, name="ajouter_depense_chargement"),
    path(
        "chargement/<int:operation_id>/ligne/<int:ligne_id>/modifier/",
        views.modifier_ligne_depense_chargement,
        name="modifier_ligne_depense_chargement",
    ),
    path("modifier/<int:id>/", views.modifier_depense, name="modifier_depense"),
    path("apercu/<int:id>/", views.apercu_depense, name="apercu_depense"),
    path("imprimer/<int:id>/", views.imprimer_depense, name="imprimer_depense"),
    path("bon-consommation/<int:id>/", views.bon_consommation_depense, name="bon_consommation_depense"),
    path("engagement/<int:id>/", views.engagement_depense, name="engagement_depense"),
    path("paiement/<int:id>/", views.paiement_depense, name="paiement_depense"),
    path("expression/valider/<int:id>/", views.valider_expression_depense, name="valider_expression_depense"),
    path("expression/rejeter/<int:id>/", views.rejeter_expression_depense, name="rejeter_expression_depense"),
    path("expression/valider-dg/<int:id>/", views.valider_expression_depense_dg, name="valider_expression_depense_dg"),
    path("expression/rejeter-dg/<int:id>/", views.rejeter_expression_depense_dg, name="rejeter_expression_depense_dg"),
    path("chargement/valider-dga/<int:id>/", views.valider_depense_chargement_dga, name="valider_depense_chargement_dga"),
    path("chargement/rejeter-dga/<int:id>/", views.rejeter_depense_chargement_dga, name="rejeter_depense_chargement_dga"),
    path("chargement/valider-dg/<int:id>/", views.valider_depense_chargement_dg, name="valider_depense_chargement_dg"),
    path("chargement/rejeter-dg/<int:id>/", views.rejeter_depense_chargement_dg, name="rejeter_depense_chargement_dg"),
    path("engagement/valider-dga/<int:id>/", views.valider_engagement_dga, name="valider_engagement_dga"),
    path("engagement/rejeter-dga/<int:id>/", views.rejeter_engagement_dga, name="rejeter_engagement_dga"),
    path("engagement/valider-dg/<int:id>/", views.valider_engagement_dg, name="valider_engagement_dg"),
    path("engagement/rejeter-dg/<int:id>/", views.rejeter_engagement_dg, name="rejeter_engagement_dg"),
    path("types/ajouter-modal/", views.ajouter_type_depense_modal, name="ajouter_type_depense_modal"),
    path("lieux/ajouter-modal/", views.ajouter_lieu_projet_modal, name="ajouter_lieu_projet_modal"),
]
