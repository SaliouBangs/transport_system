from django.db import models
from django.utils import timezone

from camions.models import Camion
from chauffeurs.models import Chauffeur
from clients.models import Client
from operations.models import Produit


class Commande(models.Model):
    STATUT_CHOICES = [
        ("attente_validation_dg", "En attente validation DG"),
        ("validee_dg", "Validee par DG"),
        ("rejetee_dg", "Rejetee par DG"),
        ("planifiee", "Planifiee"),
        ("en_cours", "En cours"),
        ("livree", "Livree"),
        ("annulee", "Annulee"),
    ]

    reference = models.CharField(max_length=50, unique=True)
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="commandes",
    )
    description = models.TextField()
    ville_depart = models.CharField(max_length=100)
    ville_arrivee = models.CharField(max_length=100)
    date_commande = models.DateField(default=timezone.localdate, editable=False)
    date_livraison_prevue = models.DateField()
    statut = models.CharField(
        max_length=30,
        choices=STATUT_CHOICES,
        default="attente_validation_dg",
    )
    produit = models.ForeignKey(
        Produit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes",
    )
    camion = models.ForeignKey(
        Camion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes",
    )
    chauffeur = models.ForeignKey(
        Chauffeur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commandes",
    )
    quantite = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    prix_negocie = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    date_validation_dg = models.DateField(null=True, blank=True)
    date_affectation_logistique = models.DateField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]

    def __str__(self):
        return self.reference
