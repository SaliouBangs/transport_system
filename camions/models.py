from django.db import models


class Transporteur(models.Model):
    nom = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Camion(models.Model):
    TYPE_CHOICES = [
        ("tracteur_citerne", "Tracteur + citerne"),
        ("camion_citerne_benne", "Camion citerne benne"),
    ]

    ETAT_CHOICES = [
        ("disponible", "Disponible"),
        ("mission", "En mission"),
        ("au_garage", "Au garage"),
    ]

    numero_tracteur = models.CharField(max_length=50, unique=True)
    numero_citerne = models.CharField(max_length=50, blank=True)
    type_camion = models.CharField(max_length=30, choices=TYPE_CHOICES, default="tracteur_citerne")
    marque = models.CharField(max_length=100, blank=True)
    modele = models.CharField(max_length=120, blank=True)
    annee = models.PositiveIntegerField(null=True, blank=True)
    carburant = models.CharField(max_length=50, blank=True)
    chassis = models.CharField(max_length=120, blank=True)
    imsi = models.CharField(max_length=100, blank=True)
    numero_sim = models.CharField(max_length=60, blank=True)
    longueur = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    largeur = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    hauteur = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    numero_balise = models.CharField(max_length=100, blank=True)
    transporteur = models.ForeignKey(
        Transporteur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camions",
    )
    capacite = models.IntegerField()
    kilometrage_actuel = models.PositiveIntegerField(default=0)
    kilometrage_alerte_vidange = models.PositiveIntegerField(null=True, blank=True)
    kilometrage_derniere_vidange = models.PositiveIntegerField(null=True, blank=True)
    etat = models.CharField(max_length=20, choices=ETAT_CHOICES, default="disponible")

    def __str__(self):
        return self.numero_tracteur
