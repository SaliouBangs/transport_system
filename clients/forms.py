from django import forms
from django.contrib.auth.models import User

from utilisateurs.constants import ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL
from utilisateurs.permissions import get_user_role
from .models import Client


class ClientForm(forms.ModelForm):
    commercial = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Commercial responsable",
    )

    class Meta:
        model = Client
        fields = ["commercial", "prospect", "nom", "telephone", "entreprise", "ville", "adresse", "observation"]

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["commercial"].queryset = User.objects.filter(
            groups__name__in=[ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL]
        ).distinct().order_by("first_name", "last_name", "username")

        role = get_user_role(user) if user else ""
        if role == ROLE_COMMERCIAL:
            self.fields["commercial"].initial = user
            self.fields["commercial"].widget = forms.HiddenInput()
            self.fields["commercial"].required = False
