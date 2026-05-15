from django import forms

from chauffeurs.models import Chauffeur

from .models import Camion, Transporteur


class TransporteurForm(forms.ModelForm):
    class Meta:
        model = Transporteur
        fields = ["nom", "telephone"]

class CamionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code_camion"].required = True

    class Meta:
        model = Camion
        fields = [
            "code_camion",
            "numero_tracteur",
            "numero_citerne",
            "type_camion",
            "marque",
            "modele",
            "annee",
            "carburant",
            "chassis",
            "imsi",
            "numero_sim",
            "longueur",
            "largeur",
            "hauteur",
            "numero_balise",
            "transporteur",
            "capacite",
            "kilometrage_actuel",
            "etat",
        ]


class AffreteAdminCreationForm(forms.Form):
    entreprise_nom = forms.CharField(max_length=200, label="Entreprise affretee")
    entreprise_telephone = forms.CharField(max_length=50, required=False, label="Telephone entreprise")
    numero_tracteur = forms.CharField(max_length=50, label="Camion / tracteur")
    numero_citerne = forms.CharField(max_length=50, required=False, label="Citerne")
    capacite = forms.IntegerField(min_value=1, label="Capacite")
    chauffeur_nom = forms.CharField(max_length=100, label="Nom du chauffeur")
    chauffeur_telephone = forms.CharField(max_length=20, required=False, label="Telephone chauffeur")

    def clean_numero_tracteur(self):
        numero_tracteur = (self.cleaned_data.get("numero_tracteur") or "").strip().upper()
        if Camion.objects.filter(numero_tracteur__iexact=numero_tracteur).exists():
            raise forms.ValidationError("Ce camion existe deja.")
        return numero_tracteur

    def clean_numero_citerne(self):
        numero_citerne = (self.cleaned_data.get("numero_citerne") or "").strip().upper()
        if numero_citerne and Camion.objects.filter(numero_citerne__iexact=numero_citerne).exists():
            raise forms.ValidationError("Cette citerne existe deja.")
        return numero_citerne

    def save(self):
        entreprise_nom = (self.cleaned_data["entreprise_nom"] or "").strip()
        entreprise_telephone = (self.cleaned_data["entreprise_telephone"] or "").strip()
        transporteur, created = Transporteur.objects.get_or_create(
            nom=entreprise_nom,
            defaults={"telephone": entreprise_telephone},
        )
        if not created and entreprise_telephone and transporteur.telephone != entreprise_telephone:
            transporteur.telephone = entreprise_telephone
            transporteur.save(update_fields=["telephone"])

        camion = Camion.objects.create(
            numero_tracteur=self.cleaned_data["numero_tracteur"],
            numero_citerne=self.cleaned_data["numero_citerne"],
            capacite=self.cleaned_data["capacite"],
            transporteur=transporteur,
            est_affrete=True,
            etat="disponible",
        )
        Chauffeur.objects.create(
            nom=(self.cleaned_data["chauffeur_nom"] or "").strip(),
            telephone=(self.cleaned_data["chauffeur_telephone"] or "").strip() or "N/A",
            camion=camion,
        )
        return camion


class AffreteCamionAdminForm(forms.Form):
    transporteur = forms.ModelChoiceField(
        queryset=Transporteur.objects.none(),
        label="Affrete existant",
    )
    numero_tracteur = forms.CharField(max_length=50, label="Camion / tracteur")
    numero_citerne = forms.CharField(max_length=50, required=False, label="Citerne")
    capacite = forms.IntegerField(min_value=1, label="Capacite")
    chauffeur_nom = forms.CharField(max_length=100, label="Nom du chauffeur")
    chauffeur_telephone = forms.CharField(max_length=20, required=False, label="Telephone chauffeur")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["transporteur"].queryset = (
            Transporteur.objects.filter(camions__est_affrete=True)
            .distinct()
            .order_by("nom")
        )

    def clean_numero_tracteur(self):
        numero_tracteur = (self.cleaned_data.get("numero_tracteur") or "").strip().upper()
        if Camion.objects.filter(numero_tracteur__iexact=numero_tracteur).exists():
            raise forms.ValidationError("Ce camion existe deja.")
        return numero_tracteur

    def clean_numero_citerne(self):
        numero_citerne = (self.cleaned_data.get("numero_citerne") or "").strip().upper()
        if numero_citerne and Camion.objects.filter(numero_citerne__iexact=numero_citerne).exists():
            raise forms.ValidationError("Cette citerne existe deja.")
        return numero_citerne

    def save(self):
        transporteur = self.cleaned_data["transporteur"]
        camion = Camion.objects.create(
            numero_tracteur=self.cleaned_data["numero_tracteur"],
            numero_citerne=self.cleaned_data["numero_citerne"],
            capacite=self.cleaned_data["capacite"],
            transporteur=transporteur,
            est_affrete=True,
            etat="disponible",
        )
        Chauffeur.objects.create(
            nom=(self.cleaned_data["chauffeur_nom"] or "").strip(),
            telephone=(self.cleaned_data["chauffeur_telephone"] or "").strip() or "N/A",
            camion=camion,
        )
        return camion
