from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.forms import inlineformset_factory

from commandes.models import Commande
from utilisateurs.constants import ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL
from utilisateurs.permissions import get_user_role
from .models import Banque, Client, ClientDestinationAdresse, EncaissementClient, total_encaisse_sur_commande


class ClientForm(forms.ModelForm):
    commercial = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Commercial responsable",
    )

    class Meta:
        model = Client
        fields = [
            "commercial",
            "prospect",
            "nom",
            "fonction_contact",
            "telephone",
            "solde_initial",
            "date_solde_initial",
            "delai_paiement_jours",
            "decouvert_maximum_autorise",
            "entreprise",
            "ville",
            "adresse",
            "observation",
        ]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["commercial"].queryset = User.objects.filter(
            groups__name__in=[ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL]
        ).exclude(is_superuser=True).distinct().order_by("first_name", "last_name", "username")

        role = get_user_role(user) if user else ""
        if role == ROLE_COMMERCIAL:
            self.fields["commercial"].initial = user
            self.fields["commercial"].widget = forms.HiddenInput()
            self.fields["commercial"].required = False

        self.fields["entreprise"].label = "Client"
        self.fields["adresse"].label = "Siege du client"
        self.fields["fonction_contact"].label = "Fonction du contact"
        self.fields["solde_initial"].label = "Solde initial"
        self.fields["date_solde_initial"].label = "Date de solde initial"
        self.fields["delai_paiement_jours"].label = "Delai de paiement (jours)"
        self.fields["decouvert_maximum_autorise"].label = "Decouvert maximum autorise"


class ClientDestinationForm(forms.ModelForm):
    class Meta:
        model = ClientDestinationAdresse
        fields = ["adresse"]
        widgets = {
            "adresse": forms.TextInput(
                attrs={
                    "placeholder": "Adresse de destination",
                }
            )
        }


ClientDestinationFormSet = inlineformset_factory(
    Client,
    ClientDestinationAdresse,
    form=ClientDestinationForm,
    extra=1,
    can_delete=True,
)


class EncaissementClientForm(forms.ModelForm):
    montant = forms.CharField(
        required=True,
        label="Montant",
        widget=forms.TextInput(
            attrs={
                "inputmode": "decimal",
                "placeholder": "Ex : 1 000 000 000",
            }
        ),
    )
    client_recherche = forms.CharField(
        required=False,
        label="Client",
        widget=forms.TextInput(
            attrs={
                "list": "clients-encaissement-list",
                "placeholder": "Commencez a saisir le client",
                "autocomplete": "off",
            }
        ),
    )
    commande_recherche = forms.CharField(
        required=False,
        label="Commande",
        widget=forms.TextInput(
            attrs={
                "list": "commandes-encaissement-list",
                "placeholder": "Commencez a saisir la commande",
                "autocomplete": "off",
            }
        ),
    )
    banque_recherche = forms.CharField(
        required=False,
        label="Banque",
        widget=forms.TextInput(
            attrs={
                "list": "banques-encaissement-list",
                "placeholder": "Selectionnez ou saisissez une banque",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = EncaissementClient
        fields = [
            "client",
            "commande",
            "type_encaissement",
            "date_encaissement",
            "montant",
            "mode_paiement",
            "reference",
            "banque",
            "nom_deposant",
            "fonction_deposant",
            "numero_deposant",
            "observation",
        ]
        widgets = {
            "client": forms.HiddenInput(),
            "commande": forms.HiddenInput(),
            "date_encaissement": forms.DateInput(attrs={"type": "date"}),
            "observation": forms.Textarea(attrs={"rows": 3}),
            "banque": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["commande"].queryset = Commande.objects.select_related("client").order_by("-date_creation")
        if self.instance and self.instance.pk and self.instance.client_id:
            self.fields["client_recherche"].initial = self.instance.client.entreprise
        if self.instance and self.instance.pk and self.instance.commande_id:
            self.fields["commande_recherche"].initial = self.instance.commande.reference
        if self.instance and self.instance.pk and self.instance.banque:
            self.fields["banque_recherche"].initial = self.instance.banque
        self.fields["reference"].label = "Reference"
        self.fields["banque"].label = "Banque"
        self.fields["nom_deposant"].label = "Nom du deposant"
        self.fields["fonction_deposant"].label = "Fonction du deposant"
        self.fields["numero_deposant"].label = "Numero du deposant"
        self.fields["type_encaissement"].label = "Nature de l'encaissement"
        self.fields["commande_recherche"].label = "Commande"
        self.fields["banque_recherche"].label = "Banque"

    def clean_montant(self):
        montant = self.data.get(self.add_prefix("montant"), "")
        raw = str(montant or "").strip()
        if not raw:
            return self.cleaned_data.get("montant")

        normalized = "".join(raw.split())
        has_comma = "," in normalized
        has_dot = "." in normalized

        if has_comma and has_dot:
            if normalized.rfind(",") > normalized.rfind("."):
                normalized = normalized.replace(".", "").replace(",", ".")
            else:
                normalized = normalized.replace(",", "")
        elif has_comma:
            normalized = normalized.replace(",", ".")

        try:
            return Decimal(normalized)
        except Exception:
            raise forms.ValidationError("Saisissez un nombre valide.")

    def clean(self):
        cleaned_data = super().clean()
        client = cleaned_data.get("client")
        client_recherche = (cleaned_data.get("client_recherche") or "").strip()
        commande = cleaned_data.get("commande")
        commande_recherche = (cleaned_data.get("commande_recherche") or "").strip()
        type_encaissement = cleaned_data.get("type_encaissement")
        banque_recherche = (cleaned_data.get("banque_recherche") or "").strip()
        montant = cleaned_data.get("montant")

        if not client and client_recherche:
            client = Client.objects.filter(entreprise__iexact=client_recherche).first()
            if client:
                cleaned_data["client"] = client

        if not cleaned_data.get("client"):
            self.add_error("client_recherche", "Selectionnez un client valide.")
            return cleaned_data

        if commande_recherche and not commande:
            commande = Commande.objects.filter(reference__iexact=commande_recherche, client=cleaned_data["client"]).first()
            if commande:
                cleaned_data["commande"] = commande

        if type_encaissement == "commande" and not cleaned_data.get("commande"):
            self.add_error("commande_recherche", "Selectionnez une commande valide.")

        if type_encaissement in {"solde_initial", "avance_client", "multi_commandes"}:
            cleaned_data["commande"] = None

        if banque_recherche:
            cleaned_data["banque"] = banque_recherche

        if montant is not None and montant <= Decimal("0.00"):
            self.add_error("montant", "Le montant doit etre strictement positif.")

        if type_encaissement == "commande" and cleaned_data.get("commande") and montant is not None:
            commande = cleaned_data["commande"]
            montant_commande = (commande.quantite or Decimal("0.00")) * (commande.prix_negocie or Decimal("0.00"))
            solde_commande = max(Decimal("0.00"), montant_commande - total_encaisse_sur_commande(commande))
            if self.instance and self.instance.pk and self.instance.type_encaissement == "commande":
                if self.instance.commande_id == commande.id:
                    solde_commande += self.instance.montant or Decimal("0.00")
            if montant > solde_commande:
                self.add_error(
                    "montant",
                    f"Le montant saisi depasse le solde restant de la commande ({solde_commande}).",
                )

        if type_encaissement == "solde_initial" and cleaned_data.get("client") and montant is not None:
            client = cleaned_data["client"]
            solde_initial_restant = client.solde_initial_restant or Decimal("0.00")
            if self.instance and self.instance.pk and self.instance.type_encaissement == "solde_initial":
                solde_initial_restant += self.instance.montant or Decimal("0.00")

            if solde_initial_restant <= Decimal("0.00"):
                self.add_error(
                    "type_encaissement",
                    "Ce client n'a plus de solde initial a regler. Utilisez plutot une avance client ou un paiement sur commande.",
                )
            elif montant > solde_initial_restant:
                self.add_error(
                    "montant",
                    f"Le montant saisi depasse le solde initial restant ({solde_initial_restant}).",
                )

        return cleaned_data


class BanqueForm(forms.ModelForm):
    class Meta:
        model = Banque
        fields = ["nom", "code"]
