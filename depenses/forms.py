from decimal import Decimal

from django import forms
from django.utils import timezone

from maintenance.models import Fournisseur

from .models import Depense, LieuProjet, TypeDepense


def _is_carburant_label(label):
    value = (label or "").lower()
    return any(keyword in value for keyword in ["carburant", "gasoil", "essence"])


CARBURANT_PRIX_UNITAIRE = Decimal("12000")


class DepenseExpressionForm(forms.ModelForm):
    class Meta:
        model = Depense
        fields = ["titre", "description", "montant_estime"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }


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
        self.type_depenses = list(TypeDepense.objects.order_by("libelle"))
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
            type_depense = TypeDepense.objects.filter(libelle__iexact=type_search).first()
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


class LieuProjetForm(forms.ModelForm):
    class Meta:
        model = LieuProjet
        fields = ["libelle"]


class DepenseEngagementForm(forms.ModelForm):
    type_depense_search = forms.CharField(label="Type de depense", required=False)
    lieu_ou_projet_search = forms.CharField(label="Lieu ou projet", required=False)
    fournisseur_search = forms.CharField(label="Fournisseur", required=False)

    class Meta:
        model = Depense
        fields = [
            "type_depense",
            "lieu_projet_ref",
            "lieu_ou_projet",
            "fournisseur",
            "numero_facture",
            "piece_justificative",
            "engagement_observation",
        ]
        widgets = {
            "engagement_observation": forms.Textarea(attrs={"rows": 4}),
            "type_depense": forms.HiddenInput(),
            "lieu_projet_ref": forms.HiddenInput(),
            "lieu_ou_projet": forms.HiddenInput(),
            "fournisseur": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.type_depenses = list(TypeDepense.objects.order_by("libelle"))
        self.lieux_projets = list(LieuProjet.objects.order_by("libelle"))
        self.fournisseurs = list(Fournisseur.objects.order_by("nom_fournisseur", "entreprise"))
        if self.instance.pk and self.instance.fournisseur_id:
            self.fields["fournisseur_search"].initial = str(self.instance.fournisseur)
        if self.instance.pk and self.instance.type_depense_id:
            self.fields["type_depense_search"].initial = str(self.instance.type_depense)
        if self.instance.pk and self.instance.lieu_ou_projet:
            self.fields["lieu_ou_projet_search"].initial = self.instance.lieu_ou_projet

    def clean(self):
        cleaned_data = super().clean()
        type_depense = cleaned_data.get("type_depense")
        type_search = (cleaned_data.get("type_depense_search") or "").strip()
        if not type_depense and type_search:
            type_depense = TypeDepense.objects.filter(libelle__iexact=type_search).first()
            if type_depense:
                cleaned_data["type_depense"] = type_depense

        lieu_ref = cleaned_data.get("lieu_projet_ref")
        lieu_search = (cleaned_data.get("lieu_ou_projet_search") or "").strip()
        if not lieu_ref and lieu_search:
            lieu_ref = LieuProjet.objects.filter(libelle__iexact=lieu_search).first()
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
                Fournisseur.objects.filter(nom_fournisseur__iexact=search).first()
                or Fournisseur.objects.filter(entreprise__iexact=search).first()
                or Fournisseur.objects.filter(numero_telephone__iexact=search).first()
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
    class Meta:
        model = Depense
        fields = [
            "date_paiement",
            "reference_paiement",
            "mode_paiement_effectif",
            "date_cheque",
            "numero_cheque",
            "banque_cheque",
            "beneficiaire_cheque",
            "receveur_nom",
            "receveur_fonction",
            "receveur_telephone",
            "paiement_observation",
        ]
        widgets = {
            "date_paiement": forms.DateInput(attrs={"type": "date"}),
            "date_cheque": forms.DateInput(attrs={"type": "date"}),
            "paiement_observation": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.depense = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        if not self.instance.pk or not self.instance.date_paiement:
            self.fields["date_paiement"].initial = timezone.localdate()
        self.fields["date_paiement"].required = True
        if self.depense and self.depense.mode_reglement == Depense.MODE_ESPECE:
            for field_name in ["date_cheque", "numero_cheque", "banque_cheque", "beneficiaire_cheque"]:
                self.fields[field_name].required = False
        else:
            for field_name in ["date_cheque", "numero_cheque", "banque_cheque"]:
                self.fields[field_name].required = True
