from django import forms

from .models import Livraison


class LivraisonForm(forms.ModelForm):
    date_depart = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    date_arrivee = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    class Meta:
        model = Livraison
        fields = [
            "commande",
            "camion",
            "chauffeur",
            "date_depart",
            "date_arrivee",
            "statut",
            "observations",
        ]
