from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from maintenance.models import Fournisseur


class TypeDepense(models.Model):
    libelle = models.CharField(max_length=150, unique=True)
    montant_defaut = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle

    @property
    def is_carburant_type(self):
        libelle = (self.libelle or "").lower()
        return any(keyword in libelle for keyword in ["carburant", "gasoil", "essence"])


class LieuProjet(models.Model):
    libelle = models.CharField(max_length=180, unique=True)

    class Meta:
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class Depense(models.Model):
    SOURCE_GENERALE = "generale"
    SOURCE_CHARGEMENT = "chargement"
    PORTEE_BL = "bl"
    PORTEE_COMMANDE = "commande"

    STATUT_BROUILLON = "brouillon"
    STATUT_ATTENTE_VALIDATION_EXPRESSION = "attente_validation_expression"
    STATUT_ATTENTE_VALIDATION_EXPRESSION_DG = "attente_validation_expression_dg"
    STATUT_REJETEE_EXPRESSION = "rejetee_expression"
    STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA = "attente_validation_chargement_dga"
    STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG = "attente_validation_chargement_dg"
    STATUT_REJETEE_CHARGEMENT = "rejetee_chargement"
    STATUT_ATTENTE_ENGAGEMENT = "attente_engagement_achat"
    STATUT_ATTENTE_VALIDATION_DGA = "attente_validation_dga_engagement"
    STATUT_ATTENTE_VALIDATION_DG = "attente_validation_dg_engagement"
    STATUT_REJETEE_DGA = "rejetee_dga_engagement"
    STATUT_REJETEE_DG = "rejetee_dg_engagement"
    STATUT_ATTENTE_PAIEMENT_COMPTABLE = "attente_paiement_comptable"
    STATUT_ATTENTE_PAIEMENT_CAISSIERE = "attente_paiement_caissiere"
    STATUT_PAYEE = "payee"

    MODE_CHEQUE = "cheque"
    MODE_ESPECE = "espece"
    DECISION_VALIDEE = "validee"
    DECISION_REJETEE = "rejetee"

    STATUT_CHOICES = [
        (STATUT_BROUILLON, "Brouillon"),
        (STATUT_ATTENTE_VALIDATION_EXPRESSION, "En attente validation expression DGA SOGEFI"),
        (STATUT_ATTENTE_VALIDATION_EXPRESSION_DG, "En attente validation expression DG"),
        (STATUT_REJETEE_EXPRESSION, "Expression rejetee"),
        (STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA, "En attente validation depense chargement DGA"),
        (STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG, "En attente validation depense chargement DG"),
        (STATUT_REJETEE_CHARGEMENT, "Depense de chargement rejetee"),
        (STATUT_ATTENTE_ENGAGEMENT, "En attente d'engagement achat"),
        (STATUT_ATTENTE_VALIDATION_DGA, "En attente validation DGA SOGEFI"),
        (STATUT_ATTENTE_VALIDATION_DG, "En attente validation DG"),
        (STATUT_REJETEE_DGA, "Engagement rejete par DGA SOGEFI"),
        (STATUT_REJETEE_DG, "Engagement rejete par DG"),
        (STATUT_ATTENTE_PAIEMENT_COMPTABLE, "En attente traitement comptable SOGEFI"),
        (STATUT_ATTENTE_PAIEMENT_CAISSIERE, "En attente traitement caissiere"),
        (STATUT_PAYEE, "Payee"),
    ]

    MODE_PAIEMENT_CHOICES = [
        (MODE_CHEQUE, "Cheque"),
        (MODE_ESPECE, "Espece"),
    ]
    SOURCE_CHOICES = [
        (SOURCE_GENERALE, "Depense generale"),
        (SOURCE_CHARGEMENT, "Depense liee au chargement"),
    ]
    PORTEE_CHOICES = [
        (PORTEE_BL, "BL uniquement"),
        (PORTEE_COMMANDE, "Commande entiere"),
    ]
    DECISION_CHOICES = [
        (DECISION_VALIDEE, "Validee"),
        (DECISION_REJETEE, "Rejetee"),
    ]

    reference = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    source_depense = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_GENERALE,
    )
    operation = models.ForeignKey(
        "operations.Operation",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="depenses_liees",
    )
    commande = models.ForeignKey(
        "commandes.Commande",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_liees",
    )
    portee_chargement = models.CharField(
        max_length=20,
        choices=PORTEE_CHOICES,
        default=PORTEE_BL,
    )
    demandeur = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="depenses_demandees",
    )
    titre = models.CharField(max_length=200)
    libelle_depense = models.CharField(max_length=200, blank=True)
    description = models.TextField()
    montant_estime = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    date_bon_conso = models.DateField(null=True, blank=True)
    quantite_a_consommer = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    statut = models.CharField(
        max_length=40,
        choices=STATUT_CHOICES,
        default=STATUT_ATTENTE_VALIDATION_EXPRESSION,
    )

    expression_validee_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_expressions_validees",
    )
    expression_validee_le = models.DateTimeField(null=True, blank=True)
    expression_validee_role = models.CharField(max_length=40, blank=True)
    motif_rejet_expression = models.TextField(blank=True)
    expression_decision_dga = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True)
    expression_decision_dga_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_expression_decisions_dga",
    )
    expression_decision_dga_le = models.DateTimeField(null=True, blank=True)
    expression_decision_dga_motif = models.TextField(blank=True)
    expression_decision_dg = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True)
    expression_decision_dg_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_expression_decisions_dg",
    )
    expression_decision_dg_le = models.DateTimeField(null=True, blank=True)
    expression_decision_dg_motif = models.TextField(blank=True)

    type_depense = models.ForeignKey(
        TypeDepense,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses",
    )
    lieu_projet_ref = models.ForeignKey(
        LieuProjet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses",
    )
    lieu_ou_projet = models.CharField(max_length=200, blank=True)
    montant_engage = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    fournisseur = models.ForeignKey(
        Fournisseur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses",
    )
    numero_facture = models.CharField(max_length=80, blank=True)
    piece_justificative = models.FileField(upload_to="depenses/justificatifs/", blank=True)
    engagement_observation = models.TextField(blank=True)
    engagement_saisi_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_engagees",
    )
    engagement_saisi_le = models.DateTimeField(null=True, blank=True)

    validation_dga_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_validees_dga",
    )
    validation_dga_le = models.DateTimeField(null=True, blank=True)
    motif_rejet_dga = models.TextField(blank=True)
    engagement_decision_dga = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True)

    validation_dg_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_validees_dg",
    )
    validation_dg_le = models.DateTimeField(null=True, blank=True)
    motif_rejet_dg = models.TextField(blank=True)
    engagement_decision_dg = models.CharField(max_length=20, choices=DECISION_CHOICES, blank=True)
    mode_reglement = models.CharField(max_length=20, choices=MODE_PAIEMENT_CHOICES, blank=True)

    date_paiement = models.DateField(null=True, blank=True)
    reference_paiement = models.CharField(max_length=120, blank=True)
    mode_paiement_effectif = models.CharField(max_length=100, blank=True)
    date_cheque = models.DateField(null=True, blank=True)
    numero_cheque = models.CharField(max_length=80, blank=True)
    banque_cheque = models.CharField(max_length=120, blank=True)
    beneficiaire_cheque = models.CharField(max_length=150, blank=True)
    receveur_nom = models.CharField(max_length=150, blank=True)
    receveur_fonction = models.CharField(max_length=120, blank=True)
    receveur_telephone = models.CharField(max_length=50, blank=True)
    paiement_observation = models.TextField(blank=True)
    paiement_saisi_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="depenses_payees",
    )
    paiement_saisi_le = models.DateTimeField(null=True, blank=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_creation"]

    def __str__(self):
        return f"{self.reference} - {self.titre}"

    @property
    def montant_total(self):
        lignes_total = self.lignes.aggregate(total=models.Sum("montant"))["total"] if self.pk else None
        if lignes_total is not None:
            return lignes_total
        return self.montant_engage if self.montant_engage is not None else (self.montant_estime or Decimal("0"))

    @property
    def quantite_totale(self):
        quantite = self.lignes.aggregate(total=models.Sum("quantite"))["total"] if self.pk else None
        return quantite or Decimal("0")

    @property
    def est_payee(self):
        return self.statut == self.STATUT_PAYEE

    @property
    def bon_consommation_reference(self):
        return f"BC-{self.reference}" if self.reference else ""

    def est_depense_carburant(self):
        return (
            self.source_depense == self.SOURCE_CHARGEMENT
            and self.type_depense_id
            and self.type_depense.is_carburant_type
        )

    def _generate_reference(self):
        if self.reference:
            return
        last = (
            Depense.objects.exclude(reference="")
            .order_by("-id")
            .values_list("reference", flat=True)
            .first()
        )
        if not last or not last.startswith("DEP"):
            self.reference = "DEP001"
            return
        try:
            next_number = int(last.replace("DEP", "")) + 1
        except ValueError:
            next_number = (self.pk or Depense.objects.count()) + 1
        self.reference = f"DEP{next_number:03d}"

    def clean(self):
        self.titre = (self.titre or "").strip()
        self.libelle_depense = (self.libelle_depense or "").strip()
        self.description = (self.description or "").strip()
        self.lieu_ou_projet = (self.lieu_ou_projet or "").strip()
        self.numero_facture = (self.numero_facture or "").strip()
        self.engagement_observation = (self.engagement_observation or "").strip()
        self.motif_rejet_expression = (self.motif_rejet_expression or "").strip()
        self.expression_decision_dga_motif = (self.expression_decision_dga_motif or "").strip()
        self.expression_decision_dg_motif = (self.expression_decision_dg_motif or "").strip()
        self.motif_rejet_dga = (self.motif_rejet_dga or "").strip()
        self.motif_rejet_dg = (self.motif_rejet_dg or "").strip()
        self.reference_paiement = (self.reference_paiement or "").strip()
        self.mode_paiement_effectif = (self.mode_paiement_effectif or "").strip()
        self.numero_cheque = (self.numero_cheque or "").strip()
        self.banque_cheque = (self.banque_cheque or "").strip()
        self.beneficiaire_cheque = (self.beneficiaire_cheque or "").strip()
        self.receveur_nom = (self.receveur_nom or "").strip()
        self.receveur_fonction = (self.receveur_fonction or "").strip()
        self.receveur_telephone = (self.receveur_telephone or "").strip()
        self.paiement_observation = (self.paiement_observation or "").strip()

        if not self.titre:
            raise ValidationError({"titre": "Le titre de l'expression de besoin est obligatoire."})
        if not self.description:
            raise ValidationError({"description": "La description du besoin est obligatoire."})
        if self.source_depense == self.SOURCE_CHARGEMENT and self.operation_id and not self.commande_id:
            self.commande_id = self.operation.commande_id

        if self.source_depense == self.SOURCE_CHARGEMENT:
            if self.portee_chargement == self.PORTEE_COMMANDE and not self.commande_id:
                raise ValidationError({"commande": "Une depense de commande doit etre rattachee a une commande."})
            if not self.operation_id and not self.commande_id:
                raise ValidationError({"operation": "Une depense liee au chargement doit etre rattachee a un BL ou a une commande."})
            if self.operation_id and self.commande_id and self.operation.commande_id and self.operation.commande_id != self.commande_id:
                raise ValidationError({"commande": "La commande choisie doit correspondre au BL de reference."})
        else:
            self.portee_chargement = self.PORTEE_BL

        if self.montant_estime is not None and self.montant_estime < 0:
            raise ValidationError({"montant_estime": "Le montant estime ne peut pas etre negatif."})
        if self.montant_engage is not None and self.montant_engage < 0:
            raise ValidationError({"montant_engage": "Le montant engage ne peut pas etre negatif."})

        if self.source_depense == self.SOURCE_CHARGEMENT and self.statut in {
            self.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA,
            self.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG,
            self.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            self.STATUT_PAYEE,
            self.STATUT_REJETEE_CHARGEMENT,
        }:
            missing = {}
            if not self.libelle_depense:
                missing["libelle_depense"] = "Le libelle de la depense est obligatoire."
            if not self.type_depense_id:
                missing["type_depense"] = "Le type de depense est obligatoire."
            if self.montant_estime in (None, ""):
                missing["montant_estime"] = "Le montant de la depense est obligatoire."
            if missing:
                raise ValidationError(missing)

        if self.est_depense_carburant():
            if self.portee_chargement == self.PORTEE_COMMANDE:
                raise ValidationError({"portee_chargement": "Une depense carburant doit rester rattachee a un BL unique."})
            if not self.date_bon_conso:
                self.date_bon_conso = timezone.localdate()
            if self.quantite_a_consommer in (None, ""):
                raise ValidationError({"quantite_a_consommer": "La quantite a consommer est obligatoire pour une depense carburant."})
            if self.quantite_a_consommer <= 0:
                raise ValidationError({"quantite_a_consommer": "La quantite a consommer doit etre superieure a zero."})

        if self.source_depense != self.SOURCE_CHARGEMENT and self.statut in {
            self.STATUT_ATTENTE_VALIDATION_DGA,
            self.STATUT_ATTENTE_VALIDATION_DG,
            self.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            self.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            self.STATUT_PAYEE,
            self.STATUT_REJETEE_DGA,
            self.STATUT_REJETEE_DG,
        }:
            missing = {}
            if not self.type_depense_id:
                missing["type_depense"] = "Le type de depense est obligatoire."
            if not self.lieu_ou_projet:
                missing["lieu_ou_projet"] = "Le lieu ou le projet est obligatoire."
            if self.montant_engage in (None, ""):
                missing["montant_engage"] = "Le montant engage est obligatoire."
            if not self.fournisseur_id:
                missing["fournisseur"] = "Le fournisseur est obligatoire."
            if missing:
                raise ValidationError(missing)

        if self.statut in {self.STATUT_ATTENTE_PAIEMENT_COMPTABLE, self.STATUT_ATTENTE_PAIEMENT_CAISSIERE, self.STATUT_PAYEE}:
            if self.mode_reglement not in {self.MODE_CHEQUE, self.MODE_ESPECE}:
                raise ValidationError({"mode_reglement": "Le DG doit preciser cheque ou espece."})
        if self.source_depense == self.SOURCE_CHARGEMENT and self.mode_reglement == self.MODE_CHEQUE:
            raise ValidationError({"mode_reglement": "Les depenses liees au chargement sont reglees uniquement en espece."})

        if self.mode_reglement == self.MODE_CHEQUE and self.statut == self.STATUT_PAYEE:
            missing = {}
            if not self.date_cheque:
                missing["date_cheque"] = "La date du cheque est obligatoire."
            if not self.numero_cheque:
                missing["numero_cheque"] = "Le numero de cheque est obligatoire."
            if not self.banque_cheque:
                missing["banque_cheque"] = "La banque est obligatoire."
            if missing:
                raise ValidationError(missing)

        if self.statut == self.STATUT_PAYEE and not self.date_paiement:
            raise ValidationError({"date_paiement": "La date de paiement est obligatoire."})

    def save(self, *args, **kwargs):
        self._generate_reference()
        self.full_clean()
        super().save(*args, **kwargs)

    def marquer_expression_validee(self, user, role_name):
        self.expression_validee_par = user
        self.expression_validee_le = timezone.now()
        self.expression_validee_role = role_name
        self.motif_rejet_expression = ""
        self.statut = self.STATUT_ATTENTE_ENGAGEMENT

    def expression_decidee_par_dga(self):
        return bool(self.expression_decision_dga)

    def expression_decidee_par_dg(self):
        return bool(self.expression_decision_dg)

    def est_depense_chargement(self):
        return self.source_depense == self.SOURCE_CHARGEMENT

    def engagement_decide_par_dga(self):
        return bool(self.engagement_decision_dga)

    def engagement_decide_par_dg(self):
        return bool(self.engagement_decision_dg)


class DepenseLigne(models.Model):
    depense = models.ForeignKey(
        Depense,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    type_depense = models.ForeignKey(
        TypeDepense,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lignes_depense",
    )
    designation = models.CharField(max_length=200)
    commentaire = models.TextField(blank=True)
    date_bon_conso = models.DateField(null=True, blank=True)
    quantite = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def clean(self):
        self.designation = (self.designation or "").strip()
        self.commentaire = (self.commentaire or "").strip()
        if not self.designation:
            raise ValidationError({"designation": "La designation est obligatoire."})
        if self.quantite is None or self.quantite <= 0:
            raise ValidationError({"quantite": "La quantite doit etre superieure a zero."})
        if self.prix_unitaire is None or self.prix_unitaire < 0:
            raise ValidationError({"prix_unitaire": "Le prix unitaire ne peut pas etre negatif."})

    def save(self, *args, **kwargs):
        self.full_clean()
        self.montant = (self.quantite or Decimal("0")) * (self.prix_unitaire or Decimal("0"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.depense.reference} - {self.designation}"
