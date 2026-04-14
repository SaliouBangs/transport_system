from django.core.exceptions import ValidationError
from django.db import models

from camions.models import Camion
from chauffeurs.models import Chauffeur
from clients.models import Client


class Produit(models.Model):
    nom = models.CharField(max_length=200, unique=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class RegimeDouanier(models.Model):
    libelle = models.CharField(max_length=150, unique=True)
    code_regime = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["libelle"]

    def __str__(self):
        return f"{self.libelle} ({self.code_regime})"


class Depot(models.Model):
    nom = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Operation(models.Model):
    ETAT_BON_CHOICES = [
        ("initie", "Initie"),
        ("declare", "Declare"),
        ("liquide", "Liquide"),
        ("charge", "Charge"),
        ("livre", "Livre"),
    ]
    ETAT_BON_FLOW = ["initie", "declare", "liquide", "charge", "livre"]

    numero_bl = models.CharField(max_length=50, unique=True)
    etat_bon = models.CharField(max_length=30, choices=ETAT_BON_CHOICES, default="initie")
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="operations",
    )
    reference_externe = models.CharField(max_length=100, blank=True)
    regime_douanier = models.ForeignKey(
        RegimeDouanier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operations",
    )
    depot = models.ForeignKey(
        Depot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operations",
    )

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="operations")
    destination = models.CharField(max_length=200)
    camion = models.ForeignKey(
        Camion,
        on_delete=models.PROTECT,
        related_name="operations",
        null=True,
        blank=True,
    )
    chauffeur = models.ForeignKey(
        Chauffeur,
        on_delete=models.PROTECT,
        related_name="operations",
        null=True,
        blank=True,
    )

    produit = models.ForeignKey(
        Produit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operations",
    )
    quantite = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    date_bl = models.DateField(null=True, blank=True)
    date_transmission = models.DateField(null=True, blank=True)
    date_bons_liquides = models.DateField(null=True, blank=True)
    date_bons_charges = models.DateField(null=True, blank=True)
    date_bons_livres = models.DateField(null=True, blank=True)
    date_bon_retour = models.DateField(null=True, blank=True)
    date_decharge_chauffeur = models.DateField(null=True, blank=True)
    heure_decharge_chauffeur = models.TimeField(null=True, blank=True)

    livreur = models.CharField(max_length=150, blank=True)
    numero_facture = models.CharField(max_length=80, blank=True)
    date_facture = models.DateField(null=True, blank=True)
    montant_facture = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    observation = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]

    def _sync_commande_statut(self):
        if not self.commande_id:
            return

        statut_map = {
            "initie": "planifiee",
            "declare": "planifiee",
            "liquide": "planifiee",
            "charge": "en_cours",
            "livre": "livree",
        }
        nouveau_statut = statut_map.get(self.etat_bon)
        if nouveau_statut and self.commande.statut != nouveau_statut:
            self.commande.statut = nouveau_statut
            self.commande.save(update_fields=["statut"])

    def _validate_etat_transition(self):
        if not self.etat_bon:
            return

        if self.etat_bon not in self.ETAT_BON_FLOW:
            raise ValidationError({"etat_bon": "Etat du bon invalide."})

        if not self.pk:
            return

        previous = (
            Operation.objects.filter(pk=self.pk)
            .values_list("etat_bon", flat=True)
            .first()
        )
        if not previous or previous == self.etat_bon:
            return

        previous_index = self.ETAT_BON_FLOW.index(previous)
        current_index = self.ETAT_BON_FLOW.index(self.etat_bon)
        if current_index != previous_index + 1:
            raise ValidationError(
                {
                    "etat_bon": (
                        "L'ordre du bon doit etre respecte : Initie, Declare, "
                        "Liquide, Charge, Livre."
                    )
                }
            )

    def clean(self):
        self._validate_etat_transition()

        if self.etat_bon != "initie" and not self.camion_id:
            raise ValidationError(
                {"camion": "Le camion doit etre affecte avant de changer l'etat du bon."}
            )

        if self.etat_bon in {"charge", "livre"} and not self.chauffeur_id:
            raise ValidationError(
                {"chauffeur": "Le chauffeur doit etre affecte avant de charger ou livrer le bon."}
            )

        if self.commande_id and self.client_id and self.client_id != self.commande.client_id:
            raise ValidationError("Le client doit correspondre au bon de commande selectionne.")

        if self.chauffeur_id and self.camion_id and self.chauffeur.camion_id and self.chauffeur.camion_id != self.camion_id:
            raise ValidationError("Le chauffeur choisi n'est pas rattache a ce camion principal.")

        if self.date_bons_charges and self.date_bons_livres and self.date_bons_livres < self.date_bons_charges:
            raise ValidationError("La date de livraison ne peut pas etre avant la date de chargement.")

        if self.date_bons_livres and self.date_bon_retour and self.date_bon_retour < self.date_bons_livres:
            raise ValidationError("La date de retour bon ne peut pas etre avant la date de livraison.")

    @property
    def jours_voyage(self):
        if self.date_bons_charges and self.date_bons_livres:
            return (self.date_bons_livres - self.date_bons_charges).days
        return None

    @property
    def jours_retour_bon(self):
        if self.date_bons_livres and self.date_bon_retour:
            return (self.date_bon_retour - self.date_bons_livres).days
        return None

    def __str__(self):
        return self.numero_bl

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_commande_statut()
