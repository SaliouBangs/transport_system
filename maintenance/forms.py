from django import forms
from django.forms import inlineformset_factory

from .models import Fournisseur, Maintenance, MaintenanceLigne, Prestataire, TypeMaintenance


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
            "mode_paiement",
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
