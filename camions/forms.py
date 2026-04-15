from django import forms

from .models import Camion, Transporteur


class TransporteurForm(forms.ModelForm):
    class Meta:
        model = Transporteur
        fields = ["nom"]

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
