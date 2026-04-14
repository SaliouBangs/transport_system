from django import forms

from .models import Depot, Operation, Produit, RegimeDouanier


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

    class Meta:
        model = Operation
        fields = [
            "numero_bl",
            "commande",
            "regime_douanier",
            "depot",
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

    class Meta:
        model = Operation
        fields = [
            "numero_facture",
            "date_facture",
            "montant_facture",
            "observation",
        ]
