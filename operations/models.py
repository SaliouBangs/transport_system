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


class Sommier(models.Model):
    numero_sm = models.CharField(max_length=50, unique=True)
    date_sommier = models.DateField()
    reference_navire = models.CharField(max_length=150)
    produit = models.ForeignKey(
        Produit,
        on_delete=models.PROTECT,
        related_name="sommiers",
    )
    quantite_initiale = models.DecimalField(max_digits=12, decimal_places=2)
    quantite_disponible = models.DecimalField(max_digits=12, decimal_places=2)
    observation = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_sommier", "numero_sm"]

    def clean(self):
        if self.quantite_initiale is not None and self.quantite_initiale < 0:
            raise ValidationError("La quantite initiale du sommier ne peut pas etre negative.")
        if self.quantite_disponible is not None and self.quantite_disponible < 0:
            raise ValidationError("La quantite disponible du sommier ne peut pas etre negative.")

    def save(self, *args, **kwargs):
        if self._state.adding and self.quantite_disponible in {None, ""}:
            self.quantite_disponible = self.quantite_initiale
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.numero_sm} - {self.reference_navire}"


class Operation(models.Model):
    ETAT_BON_CHOICES = [
        ("initie", "Initie"),
        ("attente_reception_transitaire", "BL secretaire"),
        ("transmis", "Transmis"),
        ("declare", "Declare"),
        ("liquide", "Liquide"),
        ("attente_reception_logistique", "Liquides en attente validation reception logistique"),
        ("liquide_logistique", "BL liquides logistique"),
        ("liquide_chauffeur", "BL liquide chauffeur"),
        ("charge", "Charge"),
        ("livre", "Livre"),
    ]
    ETAT_BON_ALLOWED_TRANSITIONS = {
        "initie": {"attente_reception_transitaire"},
        "attente_reception_transitaire": {"transmis"},
        "transmis": {"declare"},
        "declare": {"liquide"},
        "liquide": {"attente_reception_logistique", "charge"},
        "attente_reception_logistique": {"liquide_logistique"},
        "liquide_logistique": {"liquide_chauffeur"},
        "liquide_chauffeur": {"charge"},
        "charge": {"livre"},
        "livre": set(),
    }

    numero_bl = models.CharField(max_length=50, unique=True)
    etat_bon = models.CharField(max_length=30, choices=ETAT_BON_CHOICES, default="initie")
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="operations",
    )
    remplace_par = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="anciennes_versions",
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
    sommier = models.ForeignKey(
        Sommier,
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
    quantite_livree = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    date_bl = models.DateField(null=True, blank=True)
    date_transmission_depot = models.DateField(null=True, blank=True)
    date_reception_transitaire = models.DateField(null=True, blank=True)
    date_bons_declares = models.DateField(null=True, blank=True)
    date_bons_liquides = models.DateField(null=True, blank=True)
    date_transfert_logistique = models.DateField(null=True, blank=True)
    date_reception_logistique = models.DateField(null=True, blank=True)
    date_remise_chauffeur = models.DateField(null=True, blank=True)
    date_bons_charges = models.DateField(null=True, blank=True)
    date_bons_livres = models.DateField(null=True, blank=True)
    date_bon_retour = models.DateField(null=True, blank=True)
    date_decharge_chauffeur = models.DateField(null=True, blank=True)
    heure_decharge_chauffeur = models.TimeField(null=True, blank=True)

    livreur = models.CharField(max_length=150, blank=True)
    numero_facture = models.CharField(max_length=80, blank=True)
    date_facture = models.DateField(null=True, blank=True)
    montant_facture = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    stock_sommier_deduit = models.BooleanField(default=False)
    remis_a_nom = models.CharField(max_length=150, blank=True)
    remis_a_telephone = models.CharField(max_length=50, blank=True)
    mouvement_camion = models.TextField(blank=True)
    latitude_position = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    longitude_position = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    observation = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_creation"]

    def _sync_commande_statut(self):
        if not self.commande_id:
            return

        statut_map = {
            "initie": "planifiee",
            "attente_reception_transitaire": "planifiee",
            "transmis": "planifiee",
            "declare": "planifiee",
            "liquide": "planifiee",
            "attente_reception_logistique": "planifiee",
            "liquide_logistique": "planifiee",
            "liquide_chauffeur": "planifiee",
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

        if self.etat_bon not in dict(self.ETAT_BON_CHOICES):
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

        allowed_targets = self.ETAT_BON_ALLOWED_TRANSITIONS.get(previous, set())
        if self.etat_bon not in allowed_targets:
            raise ValidationError(
                {
                    "etat_bon": (
                        "L'ordre du circuit BL doit etre respecte avant de changer cet etat."
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

        if self.sommier_id and self.produit_id and self.sommier.produit_id != self.produit_id:
            raise ValidationError("Le navire selectionne ne correspond pas au produit du BL.")

        if self.chauffeur_id and self.camion_id and self.chauffeur.camion_id and self.chauffeur.camion_id != self.camion_id:
            raise ValidationError("Le chauffeur choisi n'est pas rattache a ce camion principal.")

        if self.date_bons_livres and not self.date_bons_charges:
            raise ValidationError(
                "La date de livraison ne peut pas etre renseignee sans date de chargement."
            )

        if self.date_reception_transitaire and not self.date_transmission_depot:
            raise ValidationError(
                "La reception transitaire ne peut pas etre renseignee sans transmission au depot."
            )

        if self.date_bons_declares and not self.date_transmission_depot:
            raise ValidationError(
                "La date de declaration ne peut pas etre renseignee sans date de transmission au depot."
            )

        if self.date_bons_declares and not self.date_reception_transitaire:
            raise ValidationError(
                "La declaration ne peut pas etre renseignee sans reception transitaire."
            )

        if self.date_reception_logistique and not self.date_transfert_logistique:
            raise ValidationError(
                "La reception logistique ne peut pas etre renseignee sans transfert des BL liquides."
            )

        if self.etat_bon in {"declare", "liquide", "attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"} and not self.date_bons_declares:
            raise ValidationError(
                "La date de declaration est obligatoire avant de passer a cet etat."
            )

        if self.etat_bon in {"transmis", "declare", "liquide", "attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"} and not self.date_transmission_depot:
            raise ValidationError(
                "La date de transmission au depot est obligatoire avant de poursuivre le circuit."
            )

        if self.etat_bon in {"transmis", "declare", "liquide", "attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"} and not self.date_reception_transitaire:
            raise ValidationError(
                "La reception transitaire doit etre validee avant de poursuivre le circuit."
            )

        if self.etat_bon in {"attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"} and not self.date_bons_liquides:
            raise ValidationError(
                "La date de liquidation est obligatoire avant le transfert ou le chargement."
            )

        if self.etat_bon in {"liquide_logistique", "liquide_chauffeur"} and not self.date_reception_logistique:
            raise ValidationError(
                "La reception logistique doit etre validee avant de remettre le BL au chauffeur."
            )

        if self.etat_bon == "liquide_chauffeur" and (
            not self.remis_a_nom or not self.remis_a_telephone or not self.date_remise_chauffeur
        ):
            raise ValidationError(
                "Le nom, le telephone et la date de remise sont obligatoires pour le BL liquide chauffeur."
            )

        if self.quantite_livree is not None:
            if self.quantite_livree < 0:
                raise ValidationError("La quantite livree ne peut pas etre negative.")
            if self.quantite is not None and self.quantite_livree > self.quantite:
                raise ValidationError("La quantite livree ne peut pas depasser la quantite commandee.")

        if self.etat_bon == "livre" and not self.date_bons_charges:
            raise ValidationError(
                "Impossible de livrer ce bon sans date de chargement."
            )

        if self.etat_bon == "livre" and self.quantite_livree in {None, ""}:
            raise ValidationError("La quantite livree est obligatoire pour passer le BL en livre.")

        if self.date_bons_charges and self.date_bons_livres and self.date_bons_livres < self.date_bons_charges:
            raise ValidationError("La date de livraison ne peut pas etre avant la date de chargement.")

        if self.date_bons_livres and self.date_bon_retour and self.date_bon_retour < self.date_bons_livres:
            raise ValidationError("La date de retour bon ne peut pas etre avant la date de livraison.")

        if self.date_bons_declares and self.date_bons_liquides and self.date_bons_liquides < self.date_bons_declares:
            raise ValidationError("La date de liquidation ne peut pas etre avant la date de declaration.")

        if self.date_transmission_depot and self.date_reception_transitaire and self.date_reception_transitaire < self.date_transmission_depot:
            raise ValidationError("La date de reception transitaire ne peut pas etre avant la transmission au depot.")

        if self.date_bons_liquides and self.date_transfert_logistique and self.date_transfert_logistique < self.date_bons_liquides:
            raise ValidationError("La date de transfert logistique ne peut pas etre avant la liquidation.")

        if self.date_transfert_logistique and self.date_reception_logistique and self.date_reception_logistique < self.date_transfert_logistique:
            raise ValidationError("La date de reception logistique ne peut pas etre avant le transfert logistique.")

        if self.date_reception_logistique and self.date_remise_chauffeur and self.date_remise_chauffeur < self.date_reception_logistique:
            raise ValidationError("La date de remise au chauffeur ne peut pas etre avant la reception logistique.")

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

    @property
    def quantite_manquante(self):
        if self.quantite_livree is None:
            return None
        return (self.quantite or 0) - self.quantite_livree

    def __str__(self):
        return self.numero_bl

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_commande_statut()


class HistoriqueAffectationOperation(models.Model):
    operation = models.ForeignKey(
        Operation,
        on_delete=models.CASCADE,
        related_name="historiques_affectation",
    )
    ancien_camion = models.ForeignKey(
        Camion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historiques_affectation_operation",
    )
    ancien_chauffeur = models.ForeignKey(
        Chauffeur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historiques_affectation_operation",
    )
    ancien_livreur = models.CharField(max_length=150, blank=True)
    ancienne_date_decharge_chauffeur = models.DateField(null=True, blank=True)
    ancienne_heure_decharge_chauffeur = models.TimeField(null=True, blank=True)
    ancien_etat_bon = models.CharField(max_length=30, choices=Operation.ETAT_BON_CHOICES, blank=True)
    nouveau_camion = models.ForeignKey(
        Camion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nouveaux_historiques_affectation_operation",
    )
    nouveau_chauffeur = models.ForeignKey(
        Chauffeur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="nouveaux_historiques_affectation_operation",
    )
    date_changement = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_changement"]

    def __str__(self):
        return f"{self.operation.numero_bl} - {self.date_changement:%Y-%m-%d %H:%M}"
