from django.core.exceptions import ValidationError
from django.db import models
from camions.models import Camion

class Chauffeur(models.Model):
    nom = models.CharField(max_length=100)
    telephone = models.CharField(max_length=20)
    camion = models.ForeignKey(Camion, on_delete=models.SET_NULL, null=True, blank=True)

    def clean(self):
        if self.camion:
            conflit = Chauffeur.objects.filter(camion=self.camion).exclude(pk=self.pk)
            if conflit.exists():
                raise ValidationError("Ce camion est deja affecte a un autre chauffeur.")

    def __str__(self):
        return self.nom
