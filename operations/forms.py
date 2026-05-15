from decimal import Decimal

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms.models import construct_instance
from django.utils import timezone

from commandes.models import Commande

from .models import Depot, Operation, Produit, RegimeDouanier, Sommier


class StyledDateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None, format="%Y-%m-%d"):
        super().__init__(attrs=attrs, format=format)


class StyledTimeInput(forms.TimeInput):
    input_type = "time"


class SommierSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-produit-id"] = str(instance.produit_id or "")
            option["attrs"]["data-stock-qty"] = str(instance.quantite_disponible or "")
            option["attrs"]["data-reference-navire"] = instance.reference_navire or ""
            option["attrs"]["data-stock-label"] = (
                f"{instance.numero_sm} - {instance.reference_navire} - {instance.produit.nom} - {instance.quantite_disponible} dispo"
            )
        return option


class ProduitForm(forms.ModelForm):
    class Meta:
        model = Produit
        fields = ["nom"]


class RegimeDouanierForm(forms.ModelForm):
    class Meta:
        model = RegimeDouanier
        fields = ["libelle", "code_regime"]


class DepotForm(forms.ModelForm):
    class Meta:
        model = Depot
        fields = ["nom"]


class SommierForm(forms.ModelForm):
    date_sommier = forms.DateField(required=True, widget=StyledDateInput())

    class Meta:
        model = Sommier
        fields = [
            "numero_sm",
            "date_sommier",
            "reference_navire",
            "produit",
            "quantite_initiale",
            "quantite_disponible",
            "observation",
        ]


class OperationForm(forms.ModelForm):
    date_bl = forms.DateField(required=False, widget=StyledDateInput())
    date_transmission_depot = forms.DateField(required=False, widget=StyledDateInput())
    date_bons_declares = forms.DateField(required=False, widget=StyledDateInput())
    date_bons_liquides = forms.DateField(required=False, widget=StyledDateInput())
    date_bons_charges = forms.DateField(required=False, widget=StyledDateInput())
    date_bons_livres = forms.DateField(required=False, widget=StyledDateInput())
    date_bon_retour = forms.DateField(required=False, widget=StyledDateInput())
    date_decharge_chauffeur = forms.DateField(required=False, widget=StyledDateInput())
    heure_decharge_chauffeur = forms.TimeField(required=False, widget=StyledTimeInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["etat_bon"].choices = [
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

    class Meta:
        model = Operation
        fields = [
            "numero_bl",
            "etat_bon",
            "date_bl",
            "date_bons_charges",
            "date_bons_livres",
            "client",
            "destination",
            "camion",
            "chauffeur",
            "date_decharge_chauffeur",
            "heure_decharge_chauffeur",
            "livreur",
            "produit",
            "quantite",
            "observation",
        ]


class ComptableOperationForm(forms.ModelForm):
    date_bl = forms.DateField(required=False, widget=StyledDateInput())

    def __init__(self, *args, **kwargs):
        self.allow_multiple_allocations = kwargs.pop("allow_multiple_allocations", False)
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            self.fields["date_bl"].initial = timezone.localdate()
            depot_sgp = (
                Depot.objects.filter(nom__istartswith="SGP").order_by("nom").first()
                or Depot.objects.filter(nom__icontains="SGP").order_by("nom").first()
            )
            if depot_sgp:
                self.fields["depot"].initial = depot_sgp.id
        self.fields["sommier"].widget = SommierSelect()
        queryset = Sommier.objects.select_related("produit").order_by("-date_sommier", "numero_sm")
        produit_id = None

        if self.is_bound:
            produit_id = self.data.get(self.add_prefix("produit")) or None
            if not produit_id:
                commande_id = self.data.get(self.add_prefix("commande"))
                if commande_id:
                    try:
                        commande = Commande.objects.select_related("produit").get(pk=commande_id)
                        produit_id = commande.produit_id
                    except Exception:
                        produit_id = None
        elif self.instance.pk:
            produit_id = self.instance.produit_id or getattr(self.instance.commande, "produit_id", None)
        else:
            produit_id = self.initial.get("produit") or None
            if not produit_id and self.initial.get("commande"):
                commande = self.initial["commande"]
                produit_id = getattr(commande, "produit_id", None) or getattr(getattr(commande, "produit", None), "id", None)

        if produit_id:
            queryset = queryset.filter(produit_id=produit_id)

        self.fields["sommier"].queryset = queryset
        self.fields["sommier"].label_from_instance = (
            lambda sommier: f"{sommier.numero_sm} - {sommier.reference_navire} - {sommier.produit.nom} - {sommier.quantite_disponible} dispo"
        )
        if self.allow_multiple_allocations:
            self.fields["numero_bl"].required = False
            self.fields["sommier"].required = False
            self.fields["quantite"].required = False

    def _post_clean(self):
        opts = self._meta
        exclude = self._get_validation_exclusions()
        if self.allow_multiple_allocations:
            exclude = set(exclude)
            exclude.update({"numero_bl", "sommier", "quantite"})

        try:
            self.instance = construct_instance(self, self.instance, opts.fields, opts.exclude)
        except ValidationError as error:
            self._update_errors(error)
            return

        try:
            self.instance.full_clean(exclude=exclude, validate_unique=False)
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                normalized = {}
                non_field_errors = []
                for field_name, field_errors in error.error_dict.items():
                    if field_name in self.fields:
                        normalized[field_name] = field_errors
                    else:
                        non_field_errors.extend(field_errors)
                if non_field_errors:
                    normalized[NON_FIELD_ERRORS] = non_field_errors
                self._update_errors(ValidationError(normalized))
            else:
                self._update_errors(error)

        if self._validate_unique:
            self.validate_unique()

    def clean(self):
        cleaned_data = super().clean()
        sommier = cleaned_data.get("sommier")
        commande = cleaned_data.get("commande")
        produit = cleaned_data.get("produit") or getattr(commande, "produit", None)
        quantite = cleaned_data.get("quantite")
        regime = cleaned_data.get("regime_douanier")

        if commande:
            produit = commande.produit
            quantite = commande.quantite

        if regime and produit:
            regime_label = (regime.libelle or "").lower()
            produit_label = (produit.nom or "").lower()
            if "minier" in regime_label and "gasoil" not in produit_label:
                self.add_error(
                    "produit",
                    "Le regime Marche minier ne prend que le produit GASOIL. Merci de choisir GASOIL.",
                )

        if self.allow_multiple_allocations:
            return cleaned_data

        if sommier and produit and sommier.produit_id != produit.id:
            self.add_error("sommier", "Ce navire ne correspond pas au produit de cette commande.")

        if sommier and quantite is not None and sommier.quantite_disponible is not None:
            ancienne_quantite = 0
            ancienne_sommier_id = None
            if self.instance.pk:
                ancienne_quantite = self.instance.quantite or 0
                ancienne_sommier_id = self.instance.sommier_id
            quantite_disponible = sommier.quantite_disponible + (ancienne_quantite if ancienne_sommier_id == sommier.id else 0)
            if quantite > quantite_disponible:
                self.add_error(
                    "sommier",
                    f"Le navire {sommier.reference_navire} n'a que {quantite_disponible} disponible pour ce produit.",
                )

        if self.instance.pk and self.instance.stock_sommier_deduit:
            if (
                self.instance.sommier_id != getattr(sommier, "id", None)
                or Decimal(self.instance.quantite or 0) != Decimal(quantite or 0)
            ):
                raise forms.ValidationError(
                    "Le stock de ce navire a deja ete deduit a la liquidation. Tu ne peux plus modifier le navire ou la quantite depuis la comptabilite."
                )

        return cleaned_data

    class Meta:
        model = Operation
        fields = [
            "numero_bl",
            "commande",
            "regime_douanier",
            "depot",
            "sommier",
            "client",
            "destination",
            "produit",
            "quantite",
            "date_bl",
            "observation",
        ]


class LogistiqueOperationForm(forms.ModelForm):
    date_decharge_chauffeur = forms.DateField(required=False, widget=StyledDateInput())
    heure_decharge_chauffeur = forms.TimeField(required=False, widget=StyledTimeInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["camion"].required = True
        self.fields["chauffeur"].required = True

    def clean(self):
        cleaned_data = super().clean()
        camion = cleaned_data.get("camion")
        quantite = getattr(self.instance, "quantite", None)

        if camion and quantite is not None and camion.capacite is not None and quantite > camion.capacite:
            self.add_error(
                "camion",
                (
                    f"Ce camion a une capacite de {camion.capacite} alors que la commande est de "
                    f"{quantite}. Merci de modifier le camion ou de revoir la commande."
                ),
            )

        return cleaned_data

    class Meta:
        model = Operation
        fields = [
            "camion",
            "chauffeur",
            "livreur",
            "date_decharge_chauffeur",
            "heure_decharge_chauffeur",
            "observation",
        ]


class LogisticienOperationForm(forms.ModelForm):
    date_bons_charges = forms.DateField(required=False, widget=StyledDateInput())
    date_bons_livres = forms.DateField(required=False, widget=StyledDateInput())
    date_bon_retour = forms.DateField(required=False, widget=StyledDateInput())
    quantite_livree = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "0",
                "placeholder": "Quantite livree",
            }
        ),
    )
    mouvement_camion = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Resumez ici le mouvement du camion, les etapes franchies, les incidents ou observations terrain.",
            }
        ),
    )
    latitude_position = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=6,
        widget=forms.NumberInput(
            attrs={
                "step": "0.000001",
                "placeholder": "Latitude",
            }
        ),
    )
    longitude_position = forms.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=6,
        widget=forms.NumberInput(
            attrs={
                "step": "0.000001",
                "placeholder": "Longitude",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["etat_bon"].choices = Operation.ETAT_BON_CHOICES

    def clean(self):
        cleaned_data = super().clean()
        etat_bon = cleaned_data.get("etat_bon")
        date_bons_charges = cleaned_data.get("date_bons_charges") or getattr(self.instance, "date_bons_charges", None)
        date_bons_livres = cleaned_data.get("date_bons_livres")
        quantite_livree = cleaned_data.get("quantite_livree")

        if (
            self.instance.pk
            and self.instance.date_bons_livres
            and self.instance.date_bons_charges
            and cleaned_data.get("date_bons_charges")
            and cleaned_data.get("date_bons_charges") != self.instance.date_bons_charges
        ):
            self.add_error(
                "date_bons_charges",
                "La date de chargement ne peut plus etre modifiee une fois le BL livre.",
            )

        if (
            self.instance.pk
            and self.instance.date_bon_retour
            and self.instance.date_bons_livres
            and cleaned_data.get("date_bons_livres")
            and cleaned_data.get("date_bons_livres") != self.instance.date_bons_livres
        ):
            self.add_error(
                "date_bons_livres",
                "La date de livraison ne peut plus etre modifiee une fois le bon retour enregistre.",
            )

        if date_bons_livres and not date_bons_charges:
            self.add_error(
                "date_bons_livres",
                "Tu ne peux pas renseigner la date de livraison sans date de chargement.",
            )

        if etat_bon == "livre" and not date_bons_charges:
            self.add_error(
                "date_bons_charges",
                "Tu dois d'abord renseigner la date de chargement avant de livrer ce bon.",
            )

        if etat_bon == "livre":
            if quantite_livree in {None, ""}:
                self.add_error("quantite_livree", "Renseigne la quantite livree.")
            elif self.instance.quantite is not None and quantite_livree > self.instance.quantite:
                self.add_error(
                    "quantite_livree",
                    f"La quantite livree ne peut pas depasser la quantite commandee ({self.instance.quantite}).",
                )

        if cleaned_data.get("date_bon_retour") and not (date_bons_livres or cleaned_data.get("date_bons_livres")):
            self.add_error(
                "date_bon_retour",
                "Tu dois d'abord renseigner la date de livraison avant le bon retour.",
            )

        return cleaned_data

    class Meta:
        model = Operation
        fields = [
            "etat_bon",
            "date_bons_charges",
            "date_bons_livres",
            "quantite_livree",
            "date_bon_retour",
            "mouvement_camion",
            "latitude_position",
            "longitude_position",
            "observation",
        ]


class FacturationOperationForm(forms.ModelForm):
    date_facture = forms.DateField(required=False, widget=StyledDateInput())

    def _post_clean(self):
        opts = self._meta
        exclude = self._get_validation_exclusions()

        try:
            self.instance = construct_instance(self, self.instance, opts.fields, opts.exclude)
        except ValidationError as error:
            self._update_errors(error)
            return

        try:
            self.instance.full_clean(exclude=exclude, validate_unique=False)
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                normalized = {}
                non_field_errors = []
                for field_name, field_errors in error.error_dict.items():
                    if field_name in self.fields:
                        normalized[field_name] = field_errors
                    else:
                        non_field_errors.extend(field_errors)
                if non_field_errors:
                    normalized[NON_FIELD_ERRORS] = non_field_errors
                self._update_errors(ValidationError(normalized))
            else:
                self._update_errors(error)

        if self._validate_unique:
            self.validate_unique()

    class Meta:
        model = Operation
        fields = [
            "numero_facture",
            "date_facture",
            "montant_facture",
            "observation",
        ]
