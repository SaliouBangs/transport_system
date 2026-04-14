from django import forms

from .models import Document


class DocumentForm(forms.ModelForm):
    date_emission = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    date_expiration = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = Document
        fields = [
            "camion",
            "type_document",
            "numero_document",
            "date_emission",
            "date_expiration",
            "commentaire",
        ]
