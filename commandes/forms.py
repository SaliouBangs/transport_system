from django import forms

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
            "statut",
            "produit",
            "quantite",
        ]
