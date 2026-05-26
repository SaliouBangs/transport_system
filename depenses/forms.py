from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from clients.models import Banque
from maintenance.models import Fournisseur

from .models import Depense, LieuProjet, TypeDepense, TypePieceIdentite


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            cleaned_files = [single_file_clean(item, initial) for item in data if item]
            return cleaned_files
        if not data:
            return []
        return [single_file_clean(data, initial)]


def _is_carburant_label(label):
    value = (label or "").lower()
    return any(keyword in value for keyword in ["carburant", "gasoil", "essence"])


CARBURANT_PRIX_UNITAIRE = Decimal("12000")


def _types_depense_queryset(portefeuille, entite_reference=None):
    queryset = TypeDepense.objects.filter(portefeuille=portefeuille)
    if portefeuille == TypeDepense.PORTEFEUILLE_INTERNE and entite_reference:
        queryset = queryset.filter(entite_reference=entite_reference)
    return queryset.order_by("libelle")


def _fournisseurs_queryset(portefeuille, entite_reference=None):
    queryset = Fournisseur.objects.filter(portefeuille=portefeuille)
    if entite_reference:
        if portefeuille == Fournisseur.PORTEFEUILLE_LOGISTIQUE:
            queryset = queryset.filter(Q(entite_reference=entite_reference) | Q(entite_reference=""))
        else:
            queryset = queryset.filter(entite_reference=entite_reference)
    return queryset.order_by("nom_fournisseur", "entreprise")


def _engagement_fournisseur_portefeuille(entite_reference):
    if entite_reference == TypeDepense.ENTITE_SONI:
        return Fournisseur.PORTEFEUILLE_LOGISTIQUE
    return Fournisseur.PORTEFEUILLE_INTERNE


class DepenseExpressionForm(forms.ModelForm):
    date_expression = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    class Meta:
        model = Depense
        fields = ["titre", "date_expression", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_expression:
            self.initial["date_expression"] = timezone.localdate().isoformat()


class DepenseChargementForm(forms.ModelForm):
    type_depense_search = forms.CharField(label="Type de depense", required=False)

    class Meta:
        model = Depense
        fields = ["titre", "type_depense", "description", "montant_estime", "date_bon_conso", "quantite_a_consommer"]
        widgets = {
            "titre": forms.HiddenInput(),
            "type_depense": forms.HiddenInput(),
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Commentaire libre sur cette depense camion.",
                }
            ),
            "montant_estime": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "date_bon_conso": forms.DateInput(attrs={"type": "date"}),
            "quantite_a_consommer": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.type_depenses = list(_types_depense_queryset(TypeDepense.PORTEFEUILLE_LOGISTIQUE))
        for item in self.type_depenses:
            item.montant_defaut_input = format(item.montant_defaut or 0, "f")
            item.is_carburant_type_input = "true" if item.is_carburant_type else "false"
        if self.instance.pk and self.instance.type_depense_id:
            self.fields["type_depense_search"].initial = str(self.instance.type_depense)
        if not self.instance.pk and not self.initial.get("date_bon_conso"):
            self.fields["date_bon_conso"].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        type_depense = cleaned_data.get("type_depense")
        type_search = (cleaned_data.get("type_depense_search") or "").strip()
        if not type_depense and type_search:
            type_depense = _types_depense_queryset(TypeDepense.PORTEFEUILLE_LOGISTIQUE).filter(libelle__iexact=type_search).first()
            if type_depense:
                cleaned_data["type_depense"] = type_depense
        if cleaned_data.get("type_depense") and cleaned_data.get("montant_estime") in (None, ""):
            cleaned_data["montant_estime"] = cleaned_data["type_depense"].montant_defaut
        if not cleaned_data.get("type_depense"):
            self.add_error("type_depense_search", "Selectionnez ou ajoutez un type de depense.")
        if cleaned_data.get("type_depense") and _is_carburant_label(cleaned_data["type_depense"].libelle):
            if cleaned_data.get("quantite_a_consommer") in (None, ""):
                self.add_error("quantite_a_consommer", "La quantite a consommer est obligatoire pour une depense carburant.")
            else:
                cleaned_data["montant_estime"] = (cleaned_data["quantite_a_consommer"] or Decimal("0")) * CARBURANT_PRIX_UNITAIRE
        return cleaned_data


class TypeDepenseForm(forms.ModelForm):
    class Meta:
        model = TypeDepense
        fields = ["libelle", "montant_defaut"]

    def __init__(self, *args, portefeuille=None, entite_reference="", **kwargs):
        self.portefeuille = portefeuille or TypeDepense.PORTEFEUILLE_LOGISTIQUE
        self.entite_reference = entite_reference or ""
        super().__init__(*args, **kwargs)
        self.fields["montant_defaut"].required = False
        self.fields["montant_defaut"].initial = self.initial.get("montant_defaut", 0)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.portefeuille = self.portefeuille
        instance.entite_reference = self.entite_reference if self.portefeuille == TypeDepense.PORTEFEUILLE_INTERNE else ""
        if instance.montant_defaut in (None, ""):
            instance.montant_defaut = Decimal("0")
        if commit:
            instance.save()
        return instance


class LieuProjetForm(forms.ModelForm):
    class Meta:
        model = LieuProjet
        fields = ["libelle"]

    def __init__(self, *args, entite_reference=TypeDepense.ENTITE_SOGEFI, **kwargs):
        self.entite_reference = entite_reference
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.entite_reference = self.entite_reference
        if commit:
            instance.save()
        return instance


class DepenseEngagementForm(forms.ModelForm):
    type_depense_search = forms.CharField(label="Type de depense", required=False)
    lieu_ou_projet_search = forms.CharField(label="Lieu ou projet", required=False)
    fournisseur_search = forms.CharField(label="Fournisseur", required=False)
    pieces_justificatives = MultipleFileField(
        label="Pieces justificatives (photos)",
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True, "accept": "image/*,.pdf"}),
    )

    class Meta:
        model = Depense
        fields = [
            "type_depense",
            "lieu_projet_ref",
            "lieu_ou_projet",
            "fournisseur",
            "numero_facture",
            "engagement_observation",
        ]
        widgets = {
            "engagement_observation": forms.Textarea(attrs={"rows": 4}),
            "type_depense": forms.HiddenInput(),
            "lieu_projet_ref": forms.HiddenInput(),
            "lieu_ou_projet": forms.HiddenInput(),
            "fournisseur": forms.HiddenInput(),
        }

    def __init__(self, *args, entite_reference=TypeDepense.ENTITE_SOGEFI, **kwargs):
        self.entite_reference = entite_reference
        super().__init__(*args, **kwargs)
        self.fields["fournisseur_search"].widget.attrs.update({"placeholder": "Choisir un fournisseur"})
        self.fournisseur_portefeuille = _engagement_fournisseur_portefeuille(self.entite_reference)
        self.type_depenses = list(_types_depense_queryset(TypeDepense.PORTEFEUILLE_INTERNE, self.entite_reference))
        self.lieux_projets = list(LieuProjet.objects.filter(entite_reference=self.entite_reference).order_by("libelle"))
        self.fournisseurs = list(_fournisseurs_queryset(self.fournisseur_portefeuille, self.entite_reference))
        if self.instance.pk and self.instance.fournisseur_id:
            self.fields["fournisseur_search"].initial = self.instance.fournisseur.nom_fournisseur
        if self.instance.pk and self.instance.type_depense_id:
            self.fields["type_depense_search"].initial = str(self.instance.type_depense)
        if self.instance.pk and self.instance.lieu_ou_projet:
            self.fields["lieu_ou_projet_search"].initial = self.instance.lieu_ou_projet

    def clean(self):
        cleaned_data = super().clean()
        type_depense = cleaned_data.get("type_depense")
        type_search = (cleaned_data.get("type_depense_search") or "").strip()
        if not type_depense and type_search:
            type_depense = _types_depense_queryset(TypeDepense.PORTEFEUILLE_INTERNE, self.entite_reference).filter(libelle__iexact=type_search).first()
            if type_depense:
                cleaned_data["type_depense"] = type_depense

        lieu_ref = cleaned_data.get("lieu_projet_ref")
        lieu_search = (cleaned_data.get("lieu_ou_projet_search") or "").strip()
        if not lieu_ref and lieu_search:
            lieu_ref = LieuProjet.objects.filter(entite_reference=self.entite_reference, libelle__iexact=lieu_search).first()
            if lieu_ref:
                cleaned_data["lieu_projet_ref"] = lieu_ref
        if lieu_ref and not cleaned_data.get("lieu_ou_projet"):
            cleaned_data["lieu_ou_projet"] = lieu_ref.libelle
        elif lieu_search:
            cleaned_data["lieu_ou_projet"] = lieu_search

        fournisseur = cleaned_data.get("fournisseur")
        search = (cleaned_data.get("fournisseur_search") or "").strip()
        if not fournisseur and search:
            fournisseur = (
                _fournisseurs_queryset(self.fournisseur_portefeuille, self.entite_reference).filter(nom_fournisseur__iexact=search).first()
                or _fournisseurs_queryset(self.fournisseur_portefeuille, self.entite_reference).filter(entreprise__iexact=search).first()
                or _fournisseurs_queryset(self.fournisseur_portefeuille, self.entite_reference).filter(numero_telephone__iexact=search).first()
            )
            if fournisseur:
                cleaned_data["fournisseur"] = fournisseur
        if not cleaned_data.get("type_depense"):
            self.add_error("type_depense_search", "Selectionnez ou ajoutez un type de depense.")
        if not cleaned_data.get("lieu_ou_projet"):
            self.add_error("lieu_ou_projet_search", "Selectionnez ou ajoutez un lieu ou projet.")
        if not cleaned_data.get("fournisseur"):
            self.add_error("fournisseur_search", "Selectionnez ou ajoutez un fournisseur.")
        return cleaned_data


class TypePieceIdentiteForm(forms.ModelForm):
    class Meta:
        model = TypePieceIdentite
        fields = ["libelle"]


class DepenseDecisionExpressionForm(forms.Form):
    motif_rejet = forms.CharField(
        label="Motif du rejet",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class DepenseDecisionEngagementForm(forms.Form):
    mode_reglement = forms.ChoiceField(
        label="Mode de reglement decide par le DG",
        choices=Depense.MODE_PAIEMENT_CHOICES,
        required=False,
    )
    motif_rejet = forms.CharField(
        label="Motif du rejet",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class DepensePaiementForm(forms.ModelForm):
    date_paiement = forms.DateField(
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    date_cheque = forms.DateField(
        required=False,
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    class Meta:
        model = Depense
        fields = [
            "date_paiement",
            "date_cheque",
            "numero_cheque",
            "banque_cheque",
            "beneficiaire_cheque",
            "type_piece_identite",
            "numero_piece_identite",
            "receveur_nom",
            "receveur_fonction",
            "receveur_telephone",
            "paiement_observation",
        ]
        widgets = {
            "paiement_observation": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.depense = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_paiement:
            self.initial["date_paiement"] = timezone.localdate().isoformat()
        if not self.instance.pk or not self.instance.date_cheque:
            self.initial["date_cheque"] = timezone.localdate().isoformat()
        self.fields["date_paiement"].required = True
        self.fields["banque_cheque"].required = False
        self.fields["banque_cheque"].widget.attrs.update(
            {
                "list": "banques-paiement-list",
                "placeholder": "Selectionnez ou ajoutez une banque",
                "autocomplete": "off",
            }
        )
        self.fields["type_piece_identite"].queryset = TypePieceIdentite.objects.order_by("libelle")
        if self.depense and self.depense.mode_reglement == Depense.MODE_ESPECE:
            for field_name in [
                "date_cheque",
                "numero_cheque",
                "banque_cheque",
                "beneficiaire_cheque",
                "type_piece_identite",
                "numero_piece_identite",
            ]:
                self.fields[field_name].required = False
        else:
            for field_name in ["date_cheque", "numero_cheque", "banque_cheque", "type_piece_identite", "numero_piece_identite"]:
                self.fields[field_name].required = True
            self.fields["receveur_nom"].required = True

    @property
    def banques(self):
        return Banque.objects.filter(actif=True).order_by("nom")
