from django.urls import path
from . import views

urlpatterns = [
    path('', views.liste_camions, name='camions'),
    path('affretes/', views.gestion_affretes, name='gestion_affretes'),
    path('rapports/', views.rapport_camions, name='rapport_camions'),
    path('detail/<int:id>/', views.detail_camion, name='detail_camion'),
    path('detail/<int:id>/imprimer/', views.imprimer_camion, name='imprimer_camion'),
    path('ajouter/', views.ajouter_camion, name='ajouter_camion'),
    path('modifier/<int:id>/', views.modifier_camion, name='modifier_camion'),
    path('supprimer/<int:id>/', views.supprimer_camion, name='supprimer_camion'),
    path('transporteurs/ajouter-modal/', views.ajouter_transporteur_modal, name='ajouter_transporteur_modal'),
]
