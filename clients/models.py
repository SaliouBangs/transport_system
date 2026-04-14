from django.db import models


class Client(models.Model):
    prospect = models.ForeignKey(
        "prospects.Prospect",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients_convertis",
    )

    nom = models.CharField(max_length=200)

    telephone = models.CharField(max_length=20)

    entreprise = models.CharField(max_length=200, unique=True)

    ville = models.CharField(max_length=100)
    adresse = models.CharField(max_length=255, blank=True)
    observation = models.TextField(blank=True)

    date_creation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.entreprise
