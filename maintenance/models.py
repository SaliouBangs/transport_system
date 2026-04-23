from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from camions.models import Camion
from django.contrib.auth.models import User


class TypeMaintenance(models.Model):
    libelle = models.CharField(max_length=120, unique=True)

    class Meta:
        ordering = ["libelle"]

    def __str__(self):
        return self.libelle


class Fournisseur(models.Model):
    nom_fournisseur = models.CharField(max_length=150)
    entreprise = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    domaine_activite = models.CharField(max_length=150, blank=True)
    mode_paiement = models.CharField(max_length=100, blank=True)
    numero_telephone = models.CharField(max_length=50, blank=True, null=True, unique=True)

    class Meta:
        ordering = ["nom_fournisseur", "entreprise"]

    def clean(self):
        self.nom_fournisseur = (self.nom_fournisseur or "").strip()
        self.entreprise = (self.entreprise or "").strip()
        self.email = (self.email or "").strip().lower()
        self.domaine_activite = (self.domaine_activite or "").strip()
        self.mode_paiement = (self.mode_paiement or "").strip()
        self.numero_telephone = ((self.numero_telephone or "").strip() or None)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nom_fournisseur} - {self.entreprise}"


class Prestataire(models.Model):
    nom_prestataire = models.CharField(max_length=150)
    entreprise = models.CharField(max_length=150, blank=True)
    domaine_activite = models.CharField(max_length=150, blank=True)
    numero_telephone = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["nom_prestataire", "entreprise"]

    def __str__(self):
        if self.entreprise:
            return f"{self.nom_prestataire} - {self.entreprise}"
        return self.nom_prestataire


class ArticleStock(models.Model):
    code_article = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    libelle = models.CharField(max_length=150, unique=True)
    categorie = models.CharField(max_length=120, blank=True)
    unite = models.CharField(max_length=50, default="piece")
    quantite_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    seuil_alerte = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fournisseur = models.ForeignKey(
        Fournisseur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles_stock",
    )
    observation = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["libelle"]

    def _generate_code(self):
        if self.code_article:
            return
        last = (
            ArticleStock.objects.exclude(code_article="")
            .order_by("-id")
            .values_list("code_article", flat=True)
            .first()
        )
        if not last or not last.startswith("STK"):
            self.code_article = "STK001"
            return
        try:
            next_number = int(last.replace("STK", "")) + 1
        except ValueError:
            next_number = self.pk or ArticleStock.objects.count() + 1
        self.code_article = f"STK{next_number:03d}"

    @property
    def en_alerte(self):
        return self.seuil_alerte > 0 and self.quantite_stock <= self.seuil_alerte

    @property
    def valeur_stock(self):
        return (self.quantite_stock or Decimal("0")) * (self.prix_unitaire or Decimal("0"))

    @property
    def unite_principale(self):
        return self.unite

    def get_conversion_factor(self, unite_source):
        unite_source = (unite_source or "").strip().lower()
        if not unite_source or unite_source == (self.unite or "").strip().lower():
            return Decimal("1")
        conversion = self.conversions.filter(unite_source__iexact=unite_source).first()
        if conversion:
            return conversion.quantite_equivalente
        raise ValidationError(
            {"unite": f"Aucune conversion definie entre {unite_source} et {self.unite} pour {self.libelle}."}
        )

    def get_quantite_decomposee(self, quantite=None):
        quantite = Decimal(quantite if quantite is not None else (self.quantite_stock or Decimal("0")))
        conversions = list(self.conversions.order_by("-quantite_equivalente", "unite_source"))
        if not conversions:
            return f"{self._format_decimal(quantite)} {self.unite}"

        remainder = quantite
        segments = []
        for conversion in conversions:
            if conversion.quantite_equivalente <= 0:
                continue
            units_count = int(remainder // conversion.quantite_equivalente)
            if units_count > 0:
                segments.append(f"{units_count} {conversion.unite_source}")
                remainder -= conversion.quantite_equivalente * units_count
        if remainder > 0 or not segments:
            segments.append(f"{self._format_decimal(remainder)} {self.unite}")
        return " + ".join(segments)

    @staticmethod
    def _format_decimal(value):
        value = Decimal(value or 0).quantize(Decimal("0.01"))
        text = f"{value}"
        if text.endswith(".00"):
            return text[:-3]
        if text.endswith("0"):
            return text[:-1]
        return text

    def clean(self):
        self.libelle = (self.libelle or "").strip()
        self.categorie = (self.categorie or "").strip()
        self.unite = ((self.unite or "").strip().lower() or "piece")
        self.observation = (self.observation or "").strip()
        if self.quantite_stock is not None and self.quantite_stock < 0:
            raise ValidationError({"quantite_stock": "Le stock ne peut pas etre negatif."})
        if self.seuil_alerte is not None and self.seuil_alerte < 0:
            raise ValidationError({"seuil_alerte": "Le seuil d'alerte ne peut pas etre negatif."})
        if self.prix_unitaire is not None and self.prix_unitaire < 0:
            raise ValidationError({"prix_unitaire": "Le prix unitaire ne peut pas etre negatif."})

    def save(self, *args, **kwargs):
        self.full_clean()
        self._generate_code()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code_article} - {self.libelle}"


class ArticleStockConversion(models.Model):
    article = models.ForeignKey(
        ArticleStock,
        on_delete=models.CASCADE,
        related_name="conversions",
    )
    unite_source = models.CharField(max_length=50)
    quantite_equivalente = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["-quantite_equivalente", "unite_source"]
        constraints = [
            models.UniqueConstraint(
                fields=["article", "unite_source"],
                name="unique_conversion_unit_by_article",
            )
        ]

    def clean(self):
        self.unite_source = (self.unite_source or "").strip().lower()
        if not self.unite_source:
            raise ValidationError({"unite_source": "L'unite source est obligatoire."})
        if not any(char.isalpha() for char in self.unite_source):
            raise ValidationError(
                {"unite_source": "Saisissez un vrai libelle d'unite comme fut, bidon ou carton."}
            )
        if self.article_id and self.unite_source == (self.article.unite or "").strip().lower():
            raise ValidationError({"unite_source": "L'unite source doit etre differente de l'unite principale."})
        if self.quantite_equivalente is None or self.quantite_equivalente <= 0:
            raise ValidationError({"quantite_equivalente": "La conversion doit etre superieure a zero."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"1 {self.unite_source} = {ArticleStock._format_decimal(self.quantite_equivalente)} {self.article.unite}"


class MouvementStock(models.Model):
    TYPE_CHOICES = [
        ("entree", "Entree"),
        ("sortie", "Sortie"),
        ("ajustement", "Ajustement"),
    ]

    article = models.ForeignKey(
        ArticleStock,
        on_delete=models.CASCADE,
        related_name="mouvements",
    )
    type_mouvement = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantite = models.DecimalField(max_digits=12, decimal_places=2)
    quantite_saisie = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unite_saisie = models.CharField(max_length=50, blank=True)
    coefficient_conversion = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reference = models.CharField(max_length=80, blank=True)
    observation = models.TextField(blank=True)
    date_mouvement = models.DateTimeField(default=timezone.now)
    stock_avant = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    stock_apres = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mouvements_stock",
    )

    class Meta:
        ordering = ["-date_mouvement", "-id"]

    def clean(self):
        self.reference = (self.reference or "").strip()
        self.observation = (self.observation or "").strip()
        if self.quantite_saisie is None:
            self.quantite_saisie = self.quantite
        if self.quantite_saisie is None or self.quantite_saisie <= 0:
            raise ValidationError({"quantite_saisie": "La quantite doit etre superieure a zero."})
        self.unite_saisie = (self.unite_saisie or self.article.unite or "").strip().lower()
        self.coefficient_conversion = self.article.get_conversion_factor(self.unite_saisie)
        self.quantite = (self.quantite_saisie or Decimal("0")) * (self.coefficient_conversion or Decimal("1"))
        if self.type_mouvement == "sortie" and not self.reference.startswith("MAIN"):
            raise ValidationError("Les sorties de stock doivent provenir d'une maintenance validee.")
        current_stock = self.article.quantite_stock or Decimal("0")
        if self.pk:
            previous = MouvementStock.objects.filter(pk=self.pk).first()
            if previous:
                current_stock = previous.stock_avant
        if self.type_mouvement == "sortie" and self.quantite > current_stock:
            raise ValidationError({"quantite_saisie": f"Stock insuffisant. Disponible: {current_stock} {self.article.unite}."})

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not is_new:
            raise ValidationError("La modification d'un mouvement de stock n'est pas autorisee.")
        self.full_clean()
        stock_actuel = self.article.quantite_stock or Decimal("0")
        self.stock_avant = stock_actuel
        if self.type_mouvement == "entree":
            self.stock_apres = stock_actuel + self.quantite
        elif self.type_mouvement == "sortie":
            self.stock_apres = stock_actuel - self.quantite
        else:
            self.stock_apres = self.quantite
        super().save(*args, **kwargs)
        self.article.quantite_stock = self.stock_apres
        self.article.save(update_fields=["quantite_stock"])

    def __str__(self):
        return f"{self.article.libelle} - {self.get_type_mouvement_display()}"


class Maintenance(models.Model):
    STATUT_CHOICES = [
        ("en_cours", "Diagnostic en cours"),
        ("attente_prix", "En attente de saisie de prix"),
        ("attente_dga", "En attente validation DGA"),
        ("attente_dg", "En attente validation DG"),
        ("attente_paiement", "En attente de paiement"),
        ("payee", "Payee"),
        ("validee_stock", "Validee stock"),
        ("rejetee_dga", "Rejetee par le DGA"),
        ("rejetee_dg", "Rejetee par le DG"),
    ]

    reference = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    camion = models.ForeignKey(
        Camion,
        on_delete=models.CASCADE,
        related_name="maintenances",
    )
    observation = models.TextField(blank=True)
    date_debut = models.DateTimeField()
    date_fin = models.DateTimeField(null=True, blank=True)
    date_paiement = models.DateField(null=True, blank=True)
    mode_paiement = models.CharField(max_length=100, blank=True)
    kilometrage_entree = models.PositiveIntegerField(null=True, blank=True)
    kilometrage_sortie = models.PositiveIntegerField(null=True, blank=True)
    prochaine_vidange_dans_km = models.PositiveIntegerField(null=True, blank=True)
    total_facture = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    statut = models.CharField(max_length=30, choices=STATUT_CHOICES, default="en_cours")
    prestataire = models.CharField(max_length=150, blank=True)
    fournisseur = models.ForeignKey(
        Fournisseur,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenances",
    )
    numero_facture = models.CharField(max_length=80, blank=True)
    facture_fichier = models.FileField(upload_to="maintenance/factures/", blank=True)
    validation_logistique_at = models.DateTimeField(null=True, blank=True)
    validation_logistique_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logistique_validations",
    )
    validation_dga_at = models.DateTimeField(null=True, blank=True)
    validation_dga_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_dga_validations",
    )
    validation_dg_at = models.DateTimeField(null=True, blank=True)
    validation_dg_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_dg_validations",
    )
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_debut", "-date_creation"]

    @staticmethod
    def sync_camion_status(camion):
        has_active_maintenance = camion.maintenances.filter(
            statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg"]
        ).exists()
        target_state = "au_garage" if has_active_maintenance else "disponible"
        if camion.etat != target_state:
            camion.etat = target_state
            camion.save(update_fields=["etat"])

    def clean(self):
        if self.date_fin and self.date_fin < self.date_debut:
            raise ValidationError("La date de fin ne peut pas etre avant la date de debut.")
        if (
            self.kilometrage_entree is not None
            and self.kilometrage_sortie is not None
            and self.kilometrage_sortie < self.kilometrage_entree
        ):
            raise ValidationError("Le kilometrage de sortie ne peut pas etre inferieur au kilometrage d'entree.")
        if self.prochaine_vidange_dans_km is not None and self.prochaine_vidange_dans_km <= 0:
            raise ValidationError("L'intervalle de la prochaine vidange doit etre superieur a zero.")

    def _generate_reference(self):
        if self.reference:
            return

        last = (
            Maintenance.objects.exclude(reference="")
            .order_by("-id")
            .values_list("reference", flat=True)
            .first()
        )
        if not last or not last.startswith("MAIN"):
            self.reference = "MAIN001"
            return

        try:
            next_number = int(last.replace("MAIN", "")) + 1
        except ValueError:
            next_number = self.pk or Maintenance.objects.count() + 1
        self.reference = f"MAIN{next_number:03d}"

    def refresh_total_facture(self, commit=True):
        lignes_avec_pieces_ids = list(
            self.lignes.filter(sous_lignes__isnull=False).values_list("id", flat=True).distinct()
        )
        total_lignes = (
            self.lignes.exclude(id__in=lignes_avec_pieces_ids).aggregate(total=Sum("montant"))["total"]
            or Decimal("0")
        )
        total_pieces = (
            MaintenanceSousLigne.objects.filter(maintenance_ligne__maintenance=self).aggregate(total=Sum("montant"))["total"]
            or Decimal("0")
        )
        total = total_lignes + total_pieces
        self.total_facture = total
        if commit and self.pk:
            Maintenance.objects.filter(pk=self.pk).update(total_facture=total)
        return total

    def is_pricing_complete(self):
        lignes = list(self.lignes.prefetch_related("sous_lignes"))
        if not lignes:
            return False

        for ligne in lignes:
            pieces = list(ligne.sous_lignes.all())
            if pieces:
                if any(
                    not piece.article_stock_id and (piece.prix_unitaire or Decimal("0")) <= 0
                    for piece in pieces
                ):
                    return False
            elif (ligne.prix_unitaire or Decimal("0")) <= 0:
                return False
        return True

    def is_validated_by_logistique(self):
        return self.validation_logistique_at is not None

    def is_validated_by_dga(self):
        return self.validation_dga_at is not None

    def is_validated_by_dg(self):
        return self.validation_dg_at is not None

    def is_paid(self):
        return self.statut == "payee"

    def is_stock_only(self):
        lignes = list(self.lignes.prefetch_related("sous_lignes"))
        if not lignes:
            return False
        has_stock_piece = False
        for ligne in lignes:
            pieces = list(ligne.sous_lignes.all())
            if not pieces:
                return False
            for piece in pieces:
                if not piece.article_stock_id:
                    return False
                has_stock_piece = True
        return has_stock_piece

    def save(self, *args, **kwargs):
        self.full_clean()
        previous_camion_id = None
        if self.pk:
            previous_camion_id = (
                Maintenance.objects.filter(pk=self.pk)
                .values_list("camion_id", flat=True)
                .first()
            )
        self._generate_reference()
        super().save(*args, **kwargs)
        target_km = self.kilometrage_sortie if self.kilometrage_sortie is not None else self.kilometrage_entree
        if target_km is not None and self.camion.kilometrage_actuel != target_km:
            self.camion.kilometrage_actuel = target_km
        if self.prochaine_vidange_dans_km and target_km is not None:
            self.camion.kilometrage_derniere_vidange = target_km
            self.camion.kilometrage_alerte_vidange = target_km + self.prochaine_vidange_dans_km
            self.camion.save(
                update_fields=[
                    "kilometrage_actuel",
                    "kilometrage_derniere_vidange",
                    "kilometrage_alerte_vidange",
                ]
            )
        elif target_km is not None:
            self.camion.save(update_fields=["kilometrage_actuel"])
        if previous_camion_id and previous_camion_id != self.camion_id:
            previous_camion = Camion.objects.get(pk=previous_camion_id)
            self.sync_camion_status(previous_camion)
        self.sync_camion_status(self.camion)

    def delete(self, *args, **kwargs):
        camion = self.camion
        super().delete(*args, **kwargs)
        self.sync_camion_status(camion)

    def __str__(self):
        return f"{self.reference} - {self.camion.numero_tracteur}"


class MaintenanceLigne(models.Model):
    maintenance = models.ForeignKey(
        Maintenance,
        on_delete=models.CASCADE,
        related_name="lignes",
    )
    type_maintenance = models.ForeignKey(
        TypeMaintenance,
        on_delete=models.PROTECT,
        related_name="lignes_maintenance",
    )
    libelle = models.CharField(max_length=200)
    quantite = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.quantite is not None and self.quantite <= 0:
            raise ValidationError({"quantite": "La quantite doit etre superieure a zero."})
        if self.prix_unitaire is not None and self.prix_unitaire < 0:
            raise ValidationError({"prix_unitaire": "Le prix unitaire ne peut pas etre negatif."})

    def save(self, *args, **kwargs):
        self.full_clean()
        quantite = self.quantite or Decimal("0")
        prix_unitaire = self.prix_unitaire or Decimal("0")
        self.montant = quantite * prix_unitaire
        super().save(*args, **kwargs)
        self.maintenance.refresh_total_facture()

    def delete(self, *args, **kwargs):
        maintenance = self.maintenance
        super().delete(*args, **kwargs)
        maintenance.refresh_total_facture()

    def __str__(self):
        return f"{self.maintenance.reference} - {self.libelle}"


class MaintenanceSousLigne(models.Model):
    maintenance_ligne = models.ForeignKey(
        MaintenanceLigne,
        on_delete=models.CASCADE,
        related_name="sous_lignes",
    )
    article_stock = models.ForeignKey(
        ArticleStock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenances_consommees",
    )
    mouvement_stock = models.OneToOneField(
        MouvementStock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="piece_maintenance",
    )
    libelle = models.CharField(max_length=200)
    quantite = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    prix_unitaire = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    montant = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def clean(self):
        self.libelle = (self.libelle or "").strip()
        if self.article_stock and not self.libelle:
            self.libelle = self.article_stock.libelle
        if not self.libelle:
            raise ValidationError({"libelle": "Le libelle de la sous-ligne est obligatoire."})
        if self.quantite is not None and self.quantite <= 0:
            raise ValidationError({"quantite": "La quantite doit etre superieure a zero."})
        if self.prix_unitaire is not None and self.prix_unitaire < 0:
            raise ValidationError({"prix_unitaire": "Le prix unitaire ne peut pas etre negatif."})

    def save(self, *args, **kwargs):
        self.full_clean()
        quantite = self.quantite or Decimal("0")
        prix_unitaire = self.prix_unitaire or Decimal("0")
        self.montant = quantite * prix_unitaire
        super().save(*args, **kwargs)
        self.maintenance_ligne.maintenance.refresh_total_facture()

    def delete(self, *args, **kwargs):
        maintenance = self.maintenance_ligne.maintenance
        super().delete(*args, **kwargs)
        maintenance.refresh_total_facture()

    def __str__(self):
        return f"{self.maintenance_ligne.libelle} - {self.libelle}"


class AlerteFactureResolue(models.Model):
    numero_facture = models.CharField(max_length=80, unique=True)
    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alertes_factures_resolues",
    )
    resolved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-resolved_at"]

    def clean(self):
        self.numero_facture = (self.numero_facture or "").strip()
        if not self.numero_facture:
            raise ValidationError({"numero_facture": "Le numero de facture est obligatoire."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.numero_facture
