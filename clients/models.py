from django.conf import settings
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce


class Client(models.Model):
    commercial = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="portefeuille_clients",
    )
    prospect = models.ForeignKey(
        "prospects.Prospect",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="clients_convertis",
    )

    nom = models.CharField(max_length=200)
    fonction_contact = models.CharField(max_length=200, blank=True)

    telephone = models.CharField(max_length=20)
    solde_initial = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    date_solde_initial = models.DateField(null=True, blank=True)
    delai_paiement_jours = models.PositiveIntegerField(default=0)
    decouvert_maximum_autorise = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    entreprise = models.CharField(max_length=200, unique=True)

    ville = models.CharField(max_length=100)
    adresse = models.CharField(max_length=255, blank=True)
    observation = models.TextField(blank=True)

    date_creation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.entreprise

    @property
    def total_commandes_livrees(self):
        return self.total_facture

    @property
    def total_paiements(self):
        total = self.encaissements.aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return total or Decimal("0.00")

    @property
    def total_paiements_solde_initial(self):
        total_direct = self.encaissements.filter(type_encaissement="solde_initial").aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        total_reparti = self.encaissements.filter(type_encaissement="multi_commandes").aggregate(
            total=Coalesce(
                Sum("allocations__montant_affecte", filter=Q(allocations__cible_type="solde_initial")),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return (total_direct or Decimal("0.00")) + (total_reparti or Decimal("0.00"))

    @property
    def solde_initial_restant(self):
        restant = (self.solde_initial or Decimal("0.00")) - self.total_paiements_solde_initial
        return max(Decimal("0.00"), restant)

    @property
    def total_avances_recues(self):
        total_direct = self.encaissements.filter(type_encaissement="avance_client").aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        total_reparti = self.encaissements.filter(type_encaissement="multi_commandes").aggregate(
            total=Coalesce(
                Sum("allocations__montant_affecte", filter=Q(allocations__cible_type="avance_client")),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return (total_direct or Decimal("0.00")) + (total_reparti or Decimal("0.00"))

    @property
    def total_avances_affectees(self):
        total = self.encaissements.filter(type_encaissement="avance_client").aggregate(
            total=Coalesce(
                Sum("allocations__montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return total or Decimal("0.00")

    @property
    def avances_disponibles(self):
        return max(Decimal("0.00"), self.total_avances_recues - self.total_avances_affectees)

    @property
    def paiements_anticipes(self):
        return self.avances_disponibles

    @property
    def total_paye_commandes(self):
        total_direct = self.encaissements.filter(type_encaissement="commande").aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        total_affectations = EncaissementClientAllocation.objects.filter(
            commande__client=self,
            cible_type="commande",
            encaissement__type_encaissement__in=["multi_commandes", "avance_client"],
        ).aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return (total_direct or Decimal("0.00")) + (total_affectations or Decimal("0.00"))

    @property
    def total_facture(self):
        total = Decimal("0.00")
        for commande in _client_commandes_queryset(self):
            if commande_est_facturee(commande):
                total += montant_total_commande(commande)
        return total

    @property
    def encours_client(self):
        encours = self.total_facture - self.total_paye_commandes
        return max(Decimal("0.00"), encours)

    @property
    def engagement_client(self):
        total = Decimal("0.00")
        for commande in _client_commandes_queryset(self):
            if commande_est_engagement(commande):
                total += montant_total_commande(commande)
        return total

    @property
    def risque_potentiel(self):
        total = Decimal("0.00")
        for commande in _client_commandes_queryset(self):
            if commande_est_risque_potentiel(commande):
                total += montant_total_commande(commande)
        return total

    @property
    def reste_a_encaisser_reel(self):
        return self.encours_client

    @property
    def exposition_client_totale(self):
        exposition = (
            self.solde_initial_restant
            + self.encours_client
            + self.engagement_client
            + self.risque_potentiel
            - self.avances_disponibles
        )
        return max(Decimal("0.00"), exposition)

    @property
    def total_paye_global(self):
        return self.total_paiements

    @property
    def engagement_net(self):
        return self.engagement_client

    @property
    def risque_client(self):
        return self.exposition_client_totale

    @property
    def creance_client(self):
        return self.encours_client

    @property
    def ratio_decouvert(self):
        plafond = self.decouvert_maximum_autorise or Decimal("0.00")
        encours = self.exposition_client_totale or Decimal("0.00")
        if plafond <= 0:
            return Decimal("0.00")
        if encours <= 0:
            return Decimal("0.00")
        ratio = (encours / plafond) * Decimal("100")
        return min(ratio, Decimal("100"))

    @property
    def niveau_risque(self):
        ratio = float(self.ratio_decouvert or 0)
        if ratio >= 90:
            return "critique"
        if ratio >= 70:
            return "alerte"
        return "ok"


class ClientDestinationAdresse(models.Model):
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="destinations",
    )
    adresse = models.CharField(max_length=255)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.client.entreprise} - {self.adresse}"


class Banque(models.Model):
    nom = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=30, blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


def montant_total_commande(commande):
    return (commande.quantite or Decimal("0.00")) * (commande.prix_negocie or Decimal("0.00"))


def latest_operation_for_commande(commande):
    operations_manager = getattr(commande, "operations", None)
    if operations_manager is None:
        return None

    prefetched = getattr(commande, "_prefetched_objects_cache", {}).get("operations")
    if prefetched is not None:
        sorted_operations = sorted(prefetched, key=lambda item: item.date_creation or 0, reverse=True)
        for operation in sorted_operations:
            if getattr(operation, "remplace_par_id", None) is None:
                return operation
        return sorted_operations[0] if sorted_operations else None

    return (
        operations_manager.filter(remplace_par__isnull=True).order_by("-date_creation").first()
        or operations_manager.order_by("-date_creation").first()
    )


def commande_est_facturee(commande):
    latest_operation = latest_operation_for_commande(commande)
    if latest_operation and latest_operation.etat_bon == "livre":
        return True
    if latest_operation and (latest_operation.numero_facture or latest_operation.date_facture):
        return True
    return commande.statut == "livree"


def commande_est_risque_potentiel(commande):
    return commande.statut in {"attente_validation_dga", "attente_validation_dg"}


def commande_est_engagement(commande):
    if commande.statut in {"rejetee_dg", "annulee", "livree"}:
        return False
    if commande_est_risque_potentiel(commande):
        return False
    if commande_est_facturee(commande):
        return False
    latest_operation = latest_operation_for_commande(commande)
    if latest_operation and latest_operation.etat_bon in {"initie", "declare", "liquide", "charge"}:
        return True
    return commande.statut in {"validee_dg", "planifiee", "en_cours"}


def _client_commandes_queryset(client):
    from commandes.models import Commande

    return Commande.objects.filter(client=client).prefetch_related("operations")


def total_encaisse_sur_commande(commande):
    if not commande:
        return Decimal("0.00")

    total_direct = (
        commande.encaissements_clients.filter(type_encaissement="commande").aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )
    total_reparti = (
        commande.encaissement_allocations.filter(cible_type="commande").aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )
    return total_direct + total_reparti


def dernier_encaissement_sur_commande(commande):
    if not commande:
        return None

    return (
        EncaissementClient.objects.filter(
            Q(type_encaissement="commande", commande=commande)
            | Q(allocations__commande=commande)
        )
        .distinct()
        .order_by("-date_encaissement", "-id")
        .first()
    )


class EncaissementClient(models.Model):
    MODE_PAIEMENT_CHOICES = [
        ("virement", "Virement"),
        ("cheque", "Cheque"),
        ("espece", "Espece"),
        ("mobile_money", "Mobile money"),
    ]
    TYPE_ENCAISSEMENT_CHOICES = [
        ("commande", "Paiement sur commande"),
        ("multi_commandes", "Paiement reparti sur plusieurs commandes"),
        ("avance_client", "Avance client"),
        ("solde_initial", "Paiement sur solde initial"),
    ]

    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name="encaissements",
    )
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encaissements_clients",
    )
    type_encaissement = models.CharField(
        max_length=20,
        choices=TYPE_ENCAISSEMENT_CHOICES,
        default="commande",
    )
    date_encaissement = models.DateField()
    montant = models.DecimalField(max_digits=14, decimal_places=2)
    mode_paiement = models.CharField(max_length=30, choices=MODE_PAIEMENT_CHOICES)
    reference = models.CharField(max_length=120, blank=True)
    banque = models.CharField(max_length=150, blank=True)
    nom_deposant = models.CharField(max_length=150, blank=True)
    fonction_deposant = models.CharField(max_length=150, blank=True)
    numero_deposant = models.CharField(max_length=30, blank=True)
    observation = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_encaissement", "-id"]

    def __str__(self):
        return f"{self.client.entreprise} - {self.montant}"

    @property
    def montant_affecte_total(self):
        total = self.allocations.aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        return total or Decimal("0.00")

    @property
    def montant_non_affecte(self):
        return (self.montant or Decimal("0.00")) - self.montant_affecte_total

    @property
    def commandes_resume(self):
        if self.commande_id:
            return self.commande.reference_affichee
        allocations = list(self.allocations.select_related("commande").all()[:3])
        if not allocations:
            return "Avance client" if self.type_encaissement == "avance_client" else "Solde initial"
        references = []
        for allocation in allocations:
            if allocation.cible_type == "solde_initial":
                references.append("Solde initial")
            elif allocation.cible_type == "avance_client":
                references.append("Avance client")
            elif allocation.commande_id:
                references.append(allocation.commande.reference_affichee)
        if self.allocations.count() > 3:
            references.append("...")
        return ", ".join(references)

    def clean(self):
        self.banque = (self.banque or "").strip()
        if self.type_encaissement == "commande" and not self.commande_id:
            raise ValidationError({"commande": "La commande est obligatoire pour ce type d'encaissement."})
        if self.commande_id and self.commande.client_id != self.client_id:
            raise ValidationError({"commande": "La commande selectionnee n'appartient pas a ce client."})
        if self.type_encaissement in {"avance_client", "solde_initial", "multi_commandes"}:
            self.commande = None


class EncaissementClientAllocation(models.Model):
    CIBLE_TYPE_CHOICES = [
        ("commande", "Commande"),
        ("solde_initial", "Solde initial"),
        ("avance_client", "Avance client"),
    ]

    encaissement = models.ForeignKey(
        EncaissementClient,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    cible_type = models.CharField(
        max_length=20,
        choices=CIBLE_TYPE_CHOICES,
        default="commande",
    )
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="encaissement_allocations",
    )
    montant_affecte = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        unique_together = [("encaissement", "commande")]

    def __str__(self):
        if self.cible_type == "solde_initial":
            cible = "Solde initial"
        elif self.cible_type == "avance_client":
            cible = "Avance client"
        else:
            cible = self.commande.reference_affichee if self.commande_id else "Commande"
        return f"{self.encaissement_id} -> {cible} ({self.montant_affecte})"

    def clean(self):
        if self.cible_type == "commande":
            if not self.commande_id:
                raise ValidationError({"commande": "La commande est obligatoire pour une affectation sur commande."})
            if self.encaissement_id and self.encaissement.client_id != self.commande.client_id:
                raise ValidationError({"commande": "La commande selectionnee n'appartient pas au client de cet encaissement."})
        else:
            self.commande = None
        if self.montant_affecte is not None and self.montant_affecte <= 0:
            raise ValidationError({"montant_affecte": "Le montant affecte doit etre strictement positif."})
