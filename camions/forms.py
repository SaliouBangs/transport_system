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
