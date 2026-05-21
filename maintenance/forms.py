from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from decimal import Decimal

from chauffeurs.models import Chauffeur
from clients.models import Banque
from depenses.models import TypePieceIdentite

from .models import (
    ArticleStock,
    ArticleStockConversion,
    ApprovisionnementCaisse,
    Fournisseur,
    Maintenance,
    MaintenanceFacture,
    MaintenanceLigne,
    MouvementStock,
    PanneCatalogue,
    Prestataire,
    SoldeInitialCaisse,
    TypeMaintenance,
)


def _fournisseurs_queryset(portefeuille):
    return Fournisseur.objects.filter(portefeuille=portefeuille).order_by("nom_fournisseur", "entreprise")


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
            queryset = queryset.order_by("numero_tracteur")
            self.fields["camion"].queryset = queryset
            chauffeurs_by_camion = {
                chauffeur.camion_id: chauffeur.nom
                for chauffeur in Chauffeur.objects.filter(camion__in=queryset).select_related("camion")
            }
            self.fields["camion"].empty_label = "Choisir un camion SOGEFI"
            self.fields["camion"].label_from_instance = lambda camion: " - ".join(
                part
                for part in [
                    f"{camion.numero_tracteur}{' / ' + camion.numero_citerne if camion.numero_citerne else ''}",
                    f"{camion.capacite:,.0f} L".replace(",", " "),
                    chauffeurs_by_camion.get(camion.id, "Camion non affecte"),
                ]
                if part
            )
            self.fields["camion"].widget.attrs.update(
                {
                    "class": "garage-camion-select",
                }
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
            "prestataire",
            "observation",
            "statut",
        ]


class MaintenancePaiementForm(forms.ModelForm):
    date_paiement = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    date_cheque = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_paiement:
            self.initial["date_paiement"] = timezone.localdate().isoformat()
        if not self.instance.pk or not self.instance.date_cheque:
            self.initial["date_cheque"] = timezone.localdate().isoformat()
        base_input_attrs = {
            "class": "cash-widget",
        }
        for field_name in [
            "date_paiement",
            "reference_cheque",
            "banque_cheque",
            "date_cheque",
            "beneficiaire_cheque",
            "numero_piece_identite",
            "receveur_nom",
            "receveur_poste",
            "receveur_telephone",
        ]:
            self.fields[field_name].widget.attrs.update(base_input_attrs)
        self.fields["type_piece_identite"].widget.attrs.update({"class": "cash-widget cash-widget--select"})
        self.fields["observation"].widget.attrs.update(
            {
                "class": "cash-widget cash-widget--textarea",
                "rows": 5,
                "placeholder": "Ajoutez une remarque utile uniquement si elle aide au controle ou a la justification du paiement.",
            }
        )
        self.fields["banque_cheque"].required = False
        self.fields["banque_cheque"].widget.attrs.update(
            {
                "list": "banques-paiement-list",
                "placeholder": "Selectionnez ou ajoutez une banque",
                "autocomplete": "off",
            }
        )
        self.fields["type_piece_identite"].queryset = TypePieceIdentite.objects.order_by("libelle")
        is_cheque = self.instance.mode_paiement == Maintenance.MODE_CHEQUE
        cheque_fields = [
            "reference_cheque",
            "banque_cheque",
            "date_cheque",
            "beneficiaire_cheque",
            "type_piece_identite",
            "numero_piece_identite",
        ]
        for field_name in cheque_fields:
            self.fields[field_name].required = is_cheque
        self.fields["receveur_nom"].required = True

    class Meta:
        model = Maintenance
        fields = [
            "date_paiement",
            "reference_cheque",
            "banque_cheque",
            "date_cheque",
            "beneficiaire_cheque",
            "type_piece_identite",
            "numero_piece_identite",
            "receveur_nom",
            "receveur_poste",
            "receveur_telephone",
            "observation",
        ]
        widgets = {
            "date_cheque": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    @property
    def banques(self):
        return Banque.objects.filter(actif=True).order_by("nom")


class MaintenanceDecisionPaiementForm(forms.Form):
    mode_paiement = forms.ChoiceField(
        label="Mode de reglement decide par le DG",
        choices=Maintenance.MODE_PAIEMENT_CHOICES,
        required=False,
    )


class ApprovisionnementCaisseForm(forms.ModelForm):
    date_approvisionnement = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    montant = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "data-money-input": "true",
                "placeholder": "0",
            }
        )
    )
    date_cheque = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    class Meta:
        model = ApprovisionnementCaisse
        fields = [
            "caissiere",
            "date_approvisionnement",
            "mode_approvisionnement",
            "nature_approvisionnement",
            "montant",
            "reference_cheque",
            "banque_cheque",
            "date_cheque",
            "observation",
        ]
        widgets = {
            "montant": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "observation": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, caissiere_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_approvisionnement:
            self.initial["date_approvisionnement"] = timezone.localdate().isoformat()
        if not self.instance.pk or not self.instance.date_cheque:
            self.initial["date_cheque"] = timezone.localdate().isoformat()
        if caissiere_queryset is not None:
            self.fields["caissiere"].queryset = caissiere_queryset
        self.fields["reference_cheque"].required = False
        self.fields["banque_cheque"].required = False
        self.fields["banque_cheque"].widget.attrs.update(
            {
                "list": "banques-appro-list",
                "placeholder": "Selectionnez ou ajoutez une banque",
                "autocomplete": "off",
            }
        )
        current_amount = getattr(self.instance, "montant", None)
        if current_amount not in (None, ""):
            self.initial["montant"] = f"{Decimal(current_amount):,.0f}".replace(",", " ")

    def clean_montant(self):
        raw_value = (self.cleaned_data.get("montant") or "").strip()
        normalized = raw_value.replace(" ", "").replace("\u00a0", "").replace(",", "")
        if not normalized:
            raise forms.ValidationError("Le montant est obligatoire.")
        try:
            value = Decimal(normalized)
        except Exception:
            raise forms.ValidationError("Saisissez un montant valide.")
        if value <= 0:
            raise forms.ValidationError("Le montant d'approvisionnement doit etre superieur a zero.")
        return value


class SoldeInitialCaisseForm(forms.ModelForm):
    date_reference = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    montant_initial = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "data-money-input": "true",
                "placeholder": "0",
            }
        )
    )

    class Meta:
        model = SoldeInitialCaisse
        fields = ["caissiere", "date_reference", "montant_initial", "observation"]
        widgets = {
            "montant_initial": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "observation": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, caissiere_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_reference:
            self.initial["date_reference"] = timezone.localdate().isoformat()
        if caissiere_queryset is not None:
            self.fields["caissiere"].queryset = caissiere_queryset
        current_amount = getattr(self.instance, "montant_initial", None)
        if current_amount not in (None, ""):
            self.initial["montant_initial"] = f"{Decimal(current_amount):,.0f}".replace(",", " ")

    def clean_montant_initial(self):
        raw_value = (self.cleaned_data.get("montant_initial") or "").strip()
        normalized = raw_value.replace(" ", "").replace("\u00a0", "").replace(",", "")
        if not normalized:
            raise forms.ValidationError("Le montant initial est obligatoire.")
        try:
            value = Decimal(normalized)
        except Exception:
            raise forms.ValidationError("Saisissez un montant valide.")
        if value < 0:
            raise forms.ValidationError("Le solde initial ne peut pas etre negatif.")
        return value


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

    def __init__(self, *args, portefeuille=None, **kwargs):
        self.portefeuille = portefeuille or Fournisseur.PORTEFEUILLE_LOGISTIQUE
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.portefeuille = self.portefeuille
        if commit:
            instance.save()
        return instance


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
        self.fields["fournisseur"].queryset = _fournisseurs_queryset(Fournisseur.PORTEFEUILLE_LOGISTIQUE)
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
        if "fournisseur" in self.fields:
            self.fields["fournisseur"].queryset = _fournisseurs_queryset(Fournisseur.PORTEFEUILLE_LOGISTIQUE)
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
        if "fournisseur" in self.fields:
            self.fields["fournisseur"].queryset = _fournisseurs_queryset(Fournisseur.PORTEFEUILLE_LOGISTIQUE)
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
