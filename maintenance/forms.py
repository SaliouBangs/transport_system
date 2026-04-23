from django import forms
from django.forms import inlineformset_factory

from .models import (
    ArticleStock,
    ArticleStockConversion,
    Fournisseur,
    Maintenance,
    MaintenanceLigne,
    MouvementStock,
    Prestataire,
    TypeMaintenance,
)


class MaintenanceForm(forms.ModelForm):
    date_debut = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    date_fin = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    class Meta:
        model = Maintenance
        fields = [
            "camion",
            "date_debut",
            "date_fin",
            "kilometrage_entree",
            "kilometrage_sortie",
            "prochaine_vidange_dans_km",
            "statut",
            "prestataire",
            "observation",
        ]


class MaintenanceGarageForm(MaintenanceForm):
    pass


class MaintenanceAchatForm(forms.ModelForm):
    class Meta:
        model = Maintenance
        fields = [
            "fournisseur",
            "numero_facture",
            "facture_fichier",
            "prestataire",
            "observation",
            "statut",
        ]


class MaintenancePaiementForm(forms.ModelForm):
    date_paiement = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = Maintenance
        fields = [
            "date_paiement",
            "observation",
        ]


class FournisseurForm(forms.ModelForm):
    class Meta:
        model = Fournisseur
        fields = [
            "nom_fournisseur",
            "entreprise",
            "numero_telephone",
            "email",
            "domaine_activite",
            "mode_paiement",
        ]


class PrestataireForm(forms.ModelForm):
    class Meta:
        model = Prestataire
        fields = ["nom_prestataire", "entreprise", "domaine_activite", "numero_telephone"]


class TypeMaintenanceForm(forms.ModelForm):
    class Meta:
        model = TypeMaintenance
        fields = ["libelle"]


class ArticleStockForm(forms.ModelForm):
    unite = forms.CharField(label="Unite principale")
    unite_stock_saisie = forms.CharField(
        label="Unite du stock saisi",
        required=False,
        help_text="Utilisez l'unite principale ou une unite de conversion definie ci-dessous.",
    )

    class Meta:
        model = ArticleStock
        fields = [
            "libelle",
            "categorie",
            "unite",
            "quantite_stock",
            "unite_stock_saisie",
            "seuil_alerte",
            "fournisseur",
            "observation",
        ]
        widgets = {
            "quantite_stock": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "seuil_alerte": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class MouvementStockForm(forms.ModelForm):
    quantite_saisie = forms.DecimalField(
        label="Quantite",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    unite_saisie = forms.ChoiceField(label="Unite de saisie")
    date_mouvement = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )

    def __init__(self, *args, article=None, **kwargs):
        super().__init__(*args, **kwargs)
        article = article or getattr(self.instance, "article", None)
        self.fields["type_mouvement"].choices = [
            ("entree", "Entree"),
            ("ajustement", "Ajustement"),
        ]
        if article:
            choices = [(article.unite, article.unite)]
            choices.extend(
                [
                    (conversion.unite_source, conversion.unite_source)
                    for conversion in article.conversions.all()
                ]
            )
            self.fields["unite_saisie"].choices = choices
            self.fields["unite_saisie"].initial = self.initial.get("unite_saisie") or article.unite
        else:
            self.fields["unite_saisie"].choices = []

    class Meta:
        model = MouvementStock
        fields = [
            "type_mouvement",
            "quantite_saisie",
            "unite_saisie",
            "reference",
            "date_mouvement",
            "observation",
        ]


class ArticleStockConversionForm(forms.ModelForm):
    unite_source = forms.CharField(label="Conditionnement", help_text="Ex: fut, bidon, carton")
    quantite_equivalente = forms.DecimalField(
        label="Quantite equivalente",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        help_text="Combien d'unites principales contient ce conditionnement",
    )

    class Meta:
        model = ArticleStockConversion
        fields = ["unite_source", "quantite_equivalente"]


class MaintenanceLigneForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLigne
        fields = [
            "type_maintenance",
            "libelle",
            "quantite",
            "prix_unitaire",
        ]
        widgets = {
            "quantite": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


class MaintenanceGarageLigneForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLigne
        fields = [
            "type_maintenance",
            "libelle",
        ]


class MaintenanceAchatLigneForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLigne
        fields = [
            "prix_unitaire",
        ]
        widgets = {
            "prix_unitaire": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }


BaseMaintenanceLigneFormSet = inlineformset_factory(
    Maintenance,
    MaintenanceLigne,
    form=MaintenanceLigneForm,
    extra=1,
    can_delete=True,
)

BaseMaintenanceGarageLigneFormSet = inlineformset_factory(
    Maintenance,
    MaintenanceLigne,
    form=MaintenanceGarageLigneForm,
    extra=1,
    can_delete=True,
)

BaseMaintenanceAchatLigneFormSet = inlineformset_factory(
    Maintenance,
    MaintenanceLigne,
    form=MaintenanceAchatLigneForm,
    extra=0,
    can_delete=False,
)

ArticleStockConversionFormSet = inlineformset_factory(
    ArticleStock,
    ArticleStockConversion,
    form=ArticleStockConversionForm,
    extra=1,
    can_delete=True,
)


class MaintenanceLigneFormSet(BaseMaintenanceLigneFormSet):
    def clean(self):
        super().clean()
        has_line = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if any(
                form.cleaned_data.get(field)
                for field in ("type_maintenance", "libelle", "quantite", "prix_unitaire")
            ):
                has_line = True
        if not has_line:
            raise forms.ValidationError(
                "Ajoute au moins une ligne de maintenance avec son montant."
            )


class MaintenanceGarageLigneFormSet(BaseMaintenanceGarageLigneFormSet):
    def clean(self):
        super().clean()
        has_line = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if any(
                form.cleaned_data.get(field)
                for field in ("type_maintenance", "libelle")
            ):
                has_line = True
        if not has_line:
            raise forms.ValidationError(
                "Ajoute au moins une ligne de diagnostic ou de panne."
            )


class MaintenanceAchatLigneFormSet(BaseMaintenanceAchatLigneFormSet):
    pass
