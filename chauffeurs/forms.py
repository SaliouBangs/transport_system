from django import forms

from .models import Chauffeur


class ChauffeurForm(forms.ModelForm):
    def clean_camion(self):
        camion = self.cleaned_data.get("camion")
        if not camion:
            return camion

        conflit = Chauffeur.objects.filter(camion=camion)
        if self.instance.pk:
            conflit = conflit.exclude(pk=self.instance.pk)

        if conflit.exists():
            raise forms.ValidationError("Ce camion est deja affecte a un autre chauffeur.")

        return camion

    class Meta:
        model = Chauffeur
        fields = ["nom", "telephone", "camion"]
