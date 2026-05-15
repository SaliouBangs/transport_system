from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class Prospect(models.Model):
    commercial = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portefeuille_prospects",
    )

    nom = models.CharField(max_length=200)
    fonction = models.CharField(max_length=200, blank=True)

    telephone = models.CharField(max_length=20)

    entreprise = models.CharField(max_length=200)

    ville = models.CharField(max_length=100)

    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("entreprise"),
                name="unique_prospect_entreprise_ci",
            )
        ]

    def __str__(self):
        return self.entreprise
