from django.core.exceptions import ValidationError
from django.db import models

from camions.models import Camion


class Document(models.Model):
    TYPE_CHOICES = [
        ("assurance", "Assurance"),
        ("carte_grise", "Carte grise"),
        ("visite_technique", "Visite technique"),
        ("taxe", "Taxe / vignette"),
        ("autre", "Autre"),
    ]

    camion = models.ForeignKey(
        Camion,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    type_document = models.CharField(max_length=30, choices=TYPE_CHOICES)
    numero_document = models.CharField(max_length=100)
    date_emission = models.DateField()
    date_expiration = models.DateField()
    commentaire = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date_expiration", "-date_creation"]

    def clean(self):
        if self.date_expiration < self.date_emission:
            raise ValidationError("La date d'expiration doit etre apres la date d'emission.")

    def __str__(self):
        return f"{self.camion.immatriculation} - {self.get_type_document_display()}"
