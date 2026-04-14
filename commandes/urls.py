from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_commandes, name="commandes"),
    path("ajouter/", views.ajouter_commande, name="ajouter_commande"),
    path("modifier/<int:id>/", views.modifier_commande, name="modifier_commande"),
    path("supprimer/<int:id>/", views.supprimer_commande, name="supprimer_commande"),
    path("export/xls/", views.export_commandes_xls, name="export_commandes_xls"),
    path("export/pdf/", views.export_commandes_pdf, name="export_commandes_pdf"),
]
