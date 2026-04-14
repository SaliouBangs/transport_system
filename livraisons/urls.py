from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_livraisons, name="livraisons"),
    path("ajouter/", views.ajouter_livraison, name="ajouter_livraison"),
    path("modifier/<int:id>/", views.modifier_livraison, name="modifier_livraison"),
    path("supprimer/<int:id>/", views.supprimer_livraison, name="supprimer_livraison"),
    path("export/xls/", views.export_livraisons_xls, name="export_livraisons_xls"),
    path("export/pdf/", views.export_livraisons_pdf, name="export_livraisons_pdf"),
]
