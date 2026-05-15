from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from decimal import Decimal

from .models import (
    ArticleStock,
    ArticleStockConversion,
    Fournisseur,
    Maintenance,
    MaintenanceFacture,
    MaintenanceLigne,
    MouvementStock,
    PanneCatalogue,
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "camion" in self.fields:
            queryset = self.fields["camion"].queryset.filter(est_affrete=False)
            instance_camion_id = getattr(getattr(self, "instance", None), "camion_id", None)
            if instance_camion_id:
                queryset = (queryset | self.fields["camion"].queryset.filter(pk=instance_camion_id)).distinct()
            self.fields["camion"].queryset = queryset.order_by("numero_tracteur")

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
            "prestataire",
            "observation",
            "statut",
        ]


class MaintenancePaiementForm(forms.ModelForm):
    date_paiement = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_paiement:
            self.fields["date_paiement"].initial = timezone.localdate()

    class Meta:
        model = Maintenance
        fields = [
            "date_paiement",
            "receveur_nom",
            "receveur_poste",
            "receveur_telephone",
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


class PanneCatalogueForm(forms.ModelForm):
    class Meta:
        model = PanneCatalogue
        fields = ["type_maintenance", "libelle"]


class MaintenanceFactureForm(forms.ModelForm):
    class Meta:
        model = MaintenanceFacture
        fields = ["fournisseur", "numero_facture", "facture_fichier"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fournisseur"].required = True
        self.fields["numero_facture"].required = True
        self.fields["facture_fichier"].required = not bool(getattr(self.instance, "facture_fichier", None))


class ArticleStockForm(forms.ModelForm):
    unite = forms.CharField(label="Unite principale")
    quantite_stock = forms.DecimalField(
        label="Quantite stock",
        required=False,
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
    unite_stock_saisie = forms.ChoiceField(
        label="Conditionnement / unite achetee",
        required=False,
        help_text="Choisissez le conditionnement reel d'achat : fut, bidon, carton ou l'unite principale si l'achat s'est fait directement au litre ou a la piece.",
    )
    prix_achat_saisi = forms.DecimalField(
        label="Prix du conditionnement achete",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        help_text="Exemple : si 1 fut de 200 L coute 1 000 000 GNF, saisissez 1000000 sur l'unite fut.",
    )
    remise_globale = forms.DecimalField(
        label="Remise globale",
        required=False,
        initial=0,
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
    seuil_alerte = forms.DecimalField(
        label="Seuil alerte",
        required=False,
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    class Meta:
        model = ArticleStock
        fields = [
            "libelle",
            "categorie",
            "unite",
            "quantite_stock",
            "unite_stock_saisie",
            "prix_achat_saisi",
            "remise_globale",
            "seuil_alerte",
            "fournisseur",
            "observation",
        ]
        widgets = {
            "observation": forms.Textarea(),
        }

    def __init__(self, *args, unite_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = unite_choices or []
        if not choices:
            current_unit = (self.initial.get("unite") or getattr(self.instance, "unite", "") or "piece").strip().lower()
            if current_unit:
                choices = [(current_unit, current_unit)]
        normalized_choices = []
        seen = set()
        for value, label in choices:
            key = (value or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized_choices.append((key, label))
        self.fields["unite_stock_saisie"].choices = normalized_choices
        for field_name in ["quantite_stock", "prix_achat_saisi", "remise_globale", "seuil_alerte"]:
            if field_name in self.fields:
                self.fields[field_name].localize = False
                current = self.initial.get(field_name, self.fields[field_name].initial)
                if current in (None, "") and getattr(self.instance, "pk", None):
                    if hasattr(self.instance, field_name):
                        current = getattr(self.instance, field_name)
                if current not in (None, ""):
                    self.initial[field_name] = self._format_decimal_input(current)

    @staticmethod
    def _format_decimal_input(value):
        decimal_value = Decimal(value or 0).quantize(Decimal("0.01"))
        text = format(decimal_value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text


class MouvementStockForm(forms.ModelForm):
    quantite_saisie = forms.DecimalField(
        label="Quantite",
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    unite_saisie = forms.ChoiceField(label="Unite de saisie")
    prix_conditionnement = forms.DecimalField(
        label="Prix du conditionnement",
        required=False,
        initial=0,
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
    remise = forms.DecimalField(
        label="Remise globale",
        required=False,
        initial=0,
        localize=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )
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
        self.fields["fournisseur"].required = False
        self.fields["prix_conditionnement"].required = False
        self.fields["remise"].required = False
        for field_name in ["quantite_saisie", "prix_conditionnement", "remise"]:
            self.fields[field_name].localize = False
            current = self.initial.get(field_name, self.fields[field_name].initial)
            if current not in (None, ""):
                self.initial[field_name] = ArticleStockForm._format_decimal_input(current)

    class Meta:
        model = MouvementStock
        fields = [
            "type_mouvement",
            "quantite_saisie",
            "unite_saisie",
            "prix_conditionnement",
            "remise",
            "fournisseur",
            "reference",
            "date_mouvement",
            "observation",
        ]

    def clean(self):
        cleaned_data = super().clean()
        type_mouvement = cleaned_data.get("type_mouvement")
        prix_conditionnement = cleaned_data.get("prix_conditionnement") or 0
        if type_mouvement == "entree" and prix_conditionnement <= 0:
            self.add_error("prix_conditionnement", "Le prix du conditionnement est obligatoire pour une entree de stock.")
        return cleaned_data


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


BaseMaintenanceFactureFormSet = inlineformset_factory(
    Maintenance,
    MaintenanceFacture,
    form=MaintenanceFactureForm,
    extra=1,
    can_delete=True,
)


class MaintenanceFactureFormSet(BaseMaintenanceFactureFormSet):
    def clean(self):
        super().clean()
        has_invoice = False
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            if any(form.cleaned_data.get(field) for field in ("fournisseur", "numero_facture", "facture_fichier")):
                has_invoice = True
        if not has_invoice:
            raise forms.ValidationError("Ajoutez au moins une facture fournisseur avec numero et fichier.")
