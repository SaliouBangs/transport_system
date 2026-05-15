from django import forms
from decimal import Decimal

from camions.models import Camion, Transporteur
from chauffeurs.models import Chauffeur
from clients.models import Client
from utilisateurs.permissions import get_user_role
from utilisateurs.permissions import is_admin_user

from .models import Commande


class CommandeForm(forms.ModelForm):
    date_livraison_prevue = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = Commande
        fields = [
            "client",
            "description",
            "ville_depart",
            "ville_arrivee",
            "date_livraison_prevue",
            "delai_paiement_jours",
            "produit",
            "quantite",
            "prix_negocie",
        ]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        self.instance_obj = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.order_by("entreprise", "nom")
        user_role = get_user_role(user) if user else ""
        if user and not is_admin_user(user) and user_role != "responsable_commercial":
            self.fields["client"].queryset = self.fields["client"].queryset.filter(commercial=user)
        self.fields["description"].label = "Commentaire"
        self.fields["description"].required = False
        self.fields["delai_paiement_jours"].label = "Delai d'echeance (jours)"
        self.fields["ville_arrivee"].label = "Destination"
        self.fields["produit"].required = True
        self.fields["quantite"].required = True
        self.fields["prix_negocie"].required = True
        self.fields["produit"].widget.attrs["required"] = "required"
        self.fields["quantite"].widget.attrs["required"] = "required"
        self.fields["prix_negocie"].widget.attrs["required"] = "required"
        self.fields["description"].widget.attrs.update(
            {"placeholder": "Commentaire libre sur la commande", "rows": 5}
        )
        self.fields["ville_depart"].initial = self.fields["ville_depart"].initial or "CONAKRY"
        self.fields["ville_depart"].widget.attrs.setdefault("value", "CONAKRY")
        self.fields["ville_arrivee"].widget.attrs.update(
            {
                "list": "client-destinations-list",
                "placeholder": "Choisir une destination du client",
                "autocomplete": "off",
            }
        )
        if not self.instance_obj or not getattr(self.instance_obj, "pk", None):
            self.fields["delai_paiement_jours"].initial = None

    def clean(self):
        cleaned_data = super().clean()
        client = cleaned_data.get("client")
        quantite = cleaned_data.get("quantite")
        prix_negocie = cleaned_data.get("prix_negocie")
        delai_paiement_jours = cleaned_data.get("delai_paiement_jours")

        if client and delai_paiement_jours in {None, ""}:
            cleaned_data["delai_paiement_jours"] = client.delai_paiement_jours or 0

        if cleaned_data.get("produit") is None:
            self.add_error("produit", "Le produit est obligatoire.")
        if cleaned_data.get("quantite") in {None, ""}:
            self.add_error("quantite", "La quantite est obligatoire.")
        if cleaned_data.get("prix_negocie") in {None, ""}:
            self.add_error("prix_negocie", "Le prix negocie est obligatoire.")

        return cleaned_data


class CommandeNumeroForm(forms.ModelForm):
    class Meta:
        model = Commande
        fields = ["reference"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reference"].label = "Numero de commande Sage"
        self.fields["reference"].required = True
        self.fields["reference"].widget.attrs.update(
            {
                "placeholder": "Ex : CC 654654",
                "autocomplete": "off",
            }
        )

    def clean_reference(self):
        reference = (self.cleaned_data.get("reference") or "").strip().upper()
        if not reference:
            raise forms.ValidationError("Le numero Sage est obligatoire.")
        queryset = Commande.objects.filter(reference__iexact=reference)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Ce numero de commande existe deja.")
        return reference


class CommandeAffectationForm(forms.Form):
    camion = forms.ModelChoiceField(
        queryset=Camion.objects.order_by("numero_tracteur"),
        required=True,
    )
    chauffeur = forms.ModelChoiceField(
        queryset=Chauffeur.objects.order_by("nom"),
        required=True,
    )

    def clean(self):
        cleaned_data = super().clean()
        camion = cleaned_data.get("camion")
        chauffeur = cleaned_data.get("chauffeur")
        commande = getattr(self, "commande", None)

        if chauffeur and camion and chauffeur.camion_id and chauffeur.camion_id != camion.id:
            self.add_error(
                "chauffeur",
                "Le chauffeur choisi n'est pas rattache a ce camion principal.",
            )

        if camion and not camion.est_affrete and not chauffeur:
            self.add_error(
                "chauffeur",
                "Le chauffeur est obligatoire pour un camion interne.",
            )

        if commande and camion and commande.quantite is not None and camion.capacite is not None and commande.quantite > camion.capacite:
            self.add_error(
                "camion",
                (
                    f"Ce camion a une capacite de {camion.capacite} alors que la commande est de "
                    f"{commande.quantite}. Merci de modifier le camion ou de revoir la commande."
                ),
            )

        return cleaned_data


class AffreteCreationForm(forms.Form):
    entreprise_nom = forms.CharField(max_length=200, label="Entreprise affretee")
    entreprise_telephone = forms.CharField(max_length=50, label="Telephone entreprise")
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


class AffreteCamionExistantForm(forms.Form):
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
