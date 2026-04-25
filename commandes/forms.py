from django import forms

from camions.models import Camion
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
            "reference",
            "client",
            "description",
            "ville_depart",
            "ville_arrivee",
            "date_livraison_prevue",
            "produit",
            "quantite",
            "prix_negocie",
        ]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.order_by("entreprise", "nom")
        user_role = get_user_role(user) if user else ""
        if user and not is_admin_user(user) and user_role != "responsable_commercial":
            self.fields["client"].queryset = self.fields["client"].queryset.filter(commercial=user)


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

        if commande and camion and commande.quantite is not None and camion.capacite is not None and commande.quantite > camion.capacite:
            self.add_error(
                "camion",
                (
                    f"Ce camion a une capacite de {camion.capacite} alors que la commande est de "
                    f"{commande.quantite}. Merci de modifier le camion ou de revoir la commande."
                ),
            )

        return cleaned_data
