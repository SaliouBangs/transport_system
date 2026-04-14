from django.urls import path

from . import views


urlpatterns = [
    path("", views.liste_chauffeurs, name="chauffeurs"),
    path("ajouter/", views.ajouter_chauffeur, name="ajouter_chauffeur"),
    path("modifier/<int:id>/", views.modifier_chauffeur, name="modifier_chauffeur"),
    path("supprimer/<int:id>/", views.supprimer_chauffeur, name="supprimer_chauffeur"),
]
