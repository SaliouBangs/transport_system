from django import forms
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.forms.models import construct_instance

from .models import Depot, Operation, Produit, RegimeDouanier, Sommier


class StyledDateInput(forms.DateInput):
    input_type = "date"


class StyledTimeInput(forms.TimeInput):
    input_type = "time"


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
    date_transmission = forms.DateField(required=False, widget=StyledDateInput())
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
        super().__init__(*args, **kwargs)
        self.fields["sommier"].queryset = Sommier.objects.select_related("produit").order_by("-date_sommier", "numero_sm")
        self.fields["sommier"].label_from_instance = (
            lambda sommier: f"{sommier.numero_sm} - {sommier.reference_navire} - {sommier.produit.nom} - {sommier.quantite_disponible} dispo"
        )

    def clean(self):
        cleaned_data = super().clean()
        sommier = cleaned_data.get("sommier")
        commande = cleaned_data.get("commande")
        produit = cleaned_data.get("produit") or getattr(commande, "produit", None)
        quantite = cleaned_data.get("quantite")

        if commande:
            produit = commande.produit
            quantite = commande.quantite

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["etat_bon"].choices = [
            ("charge", "Charge"),
            ("livre", "Livre"),
        ]

    def clean(self):
        cleaned_data = super().clean()
        etat_bon = cleaned_data.get("etat_bon")
        date_bons_charges = cleaned_data.get("date_bons_charges") or getattr(self.instance, "date_bons_charges", None)
        date_bons_livres = cleaned_data.get("date_bons_livres")

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

        return cleaned_data

    class Meta:
        model = Operation
        fields = [
            "etat_bon",
            "date_bons_charges",
            "date_bons_livres",
            "date_bon_retour",
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
