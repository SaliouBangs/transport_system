from django.urls import path

from . import views


urlpatterns = [
    path("", views.garage_maintenances, name="maintenances"),
    path("garage/", views.garage_maintenances, name="garage_maintenances"),
    path("garage/ajouter/", views.ajouter_maintenance_garage, name="ajouter_maintenance_garage"),
    path("garage/modifier/<int:id>/", views.modifier_maintenance_garage, name="modifier_maintenance_garage"),
    path("garage/terminer/<int:id>/", views.terminer_maintenance, name="terminer_maintenance"),
    path("garage/imprimer/<int:id>/", views.imprimer_maintenance, name="imprimer_maintenance"),
    path("achat/", views.achat_maintenances, name="achat_maintenances"),
    path("achat/modifier/<int:id>/", views.modifier_maintenance_achat, name="modifier_maintenance_achat"),
    path("supprimer/<int:id>/", views.supprimer_maintenance, name="supprimer_maintenance"),
    path(
        "types/ajouter-modal/",
        views.ajouter_type_maintenance_modal,
        name="ajouter_type_maintenance_modal",
    ),
]
