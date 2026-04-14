from django import forms

from .models import Camion, Transporteur


class TransporteurForm(forms.ModelForm):
    class Meta:
        model = Transporteur
        fields = ["nom"]

class CamionForm(forms.ModelForm):

    class Meta:
        model = Camion
        fields = [
            "numero_tracteur",
            "numero_citerne",
            "type_camion",
            "marque",
            "transporteur",
            "capacite",
            "kilometrage_actuel",
            "etat",
        ]
