from django import forms
from django.contrib.auth.models import User

from .constants import ROLE_CHOICES
from .permissions import assign_role, ensure_role_groups, get_user_role


class UtilisateurCreationForm(forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    is_superuser = forms.BooleanField(label="Administrateur", required=False)
    password1 = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmation du mot de passe", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        ensure_role_groups()
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ce nom d'utilisateur existe deja.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("password1") != cleaned_data.get("password2"):
            self.add_error("password2", "Les mots de passe ne correspondent pas.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = (self.cleaned_data["username"] or "").strip()
        user.email = (self.cleaned_data.get("email") or "").strip()
        user.is_staff = self.cleaned_data.get("is_superuser", False)
        user.is_superuser = self.cleaned_data.get("is_superuser", False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            assign_role(user, self.cleaned_data["role"])
        return user


class UtilisateurModificationForm(forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    is_superuser = forms.BooleanField(label="Administrateur", required=False)
    new_password1 = forms.CharField(label="Nouveau mot de passe", widget=forms.PasswordInput, required=False)
    new_password2 = forms.CharField(label="Confirmation du nouveau mot de passe", widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        ensure_role_groups()
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["role"].initial = get_user_role(self.instance)
            self.fields["is_superuser"].initial = self.instance.is_superuser

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        queryset = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Ce nom d'utilisateur existe deja.")
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")
        if password1 or password2:
            if password1 != password2:
                self.add_error("new_password2", "Les mots de passe ne correspondent pas.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = (self.cleaned_data["username"] or "").strip()
        user.email = (self.cleaned_data.get("email") or "").strip()
        user.is_staff = self.cleaned_data.get("is_superuser", False)
        user.is_superuser = self.cleaned_data.get("is_superuser", False)
        if self.cleaned_data.get("new_password1"):
            user.set_password(self.cleaned_data["new_password1"])
        if commit:
            user.save()
            assign_role(user, self.cleaned_data["role"])
        return user
