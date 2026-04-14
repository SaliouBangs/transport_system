from django.core.exceptions import ValidationError
from django.db import models

from camions.models import Camion
from chauffeurs.models import Chauffeur
from commandes.models import Commande


class Livraison(models.Model):
    STATUT_CHOICES = [
        ("planifiee", "Planifiee"),
        ("en_cours", "En cours"),
        ("livree", "Livree"),
        ("annulee", "Annulee"),
    ]

    commande = models.OneToOneField(
        Commande,
        on_delete=models.CASCADE,
        related_name="livraison",
    )
    camion = models.ForeignKey(
        Camion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="livraisons",
    )
    chauffeur = models.ForeignKey(
        Chauffeur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="livraisons",
    )
    date_depart = models.DateField()
    date_arrivee = models.DateField(null=True, blank=True)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="planifiee")
    observations = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]

    def clean(self):
        if self.date_arrivee and self.date_arrivee < self.date_depart:
            raise ValidationError("La date d'arrivee ne peut pas etre avant la date de depart.")

        if self.chauffeur and self.camion and self.chauffeur.camion and self.chauffeur.camion_id != self.camion_id:
            raise ValidationError("Ce chauffeur est deja associe a un autre camion.")

        statuts_actifs = ["planifiee", "en_cours"]

        if self.camion:
            conflit_camion = Livraison.objects.filter(
                camion=self.camion,
                statut__in=statuts_actifs,
            ).exclude(pk=self.pk)
            if conflit_camion.exists() and self.statut in statuts_actifs:
                raise ValidationError("Ce camion est deja affecte a une autre livraison active.")

        if self.chauffeur:
            conflit_chauffeur = Livraison.objects.filter(
                chauffeur=self.chauffeur,
                statut__in=statuts_actifs,
            ).exclude(pk=self.pk)
            if conflit_chauffeur.exists() and self.statut in statuts_actifs:
                raise ValidationError("Ce chauffeur est deja affecte a une autre livraison active.")

    def __str__(self):
        return f"{self.commande.reference} - {self.get_statut_display()}"
