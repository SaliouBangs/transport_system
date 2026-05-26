from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .constants import ROLE_CHOICES
from .models import ProfilUtilisateur
from .permissions import (
    PERMISSION_CATALOG,
    PERMISSION_KEYS,
    READONLY_PERMISSION_KEYS,
    assign_role,
    build_user_permissions,
    ensure_role_groups,
    get_user_role,
    permission_keys_for_role,
)


def _permission_choices():
    choices = []
    for category, permissions in PERMISSION_CATALOG:
        choices.append((category, permissions))
    return choices


def _save_user_permission_overrides(user, selected_permissions, readonly_permissions, role_name, is_superuser=False):
    selected = set(selected_permissions or [])
    readonly = set(readonly_permissions or [])
    base_permissions = set(permission_keys_for_role(role_name, is_superuser=is_superuser))
    enabled_permissions = [permission for permission in PERMISSION_KEYS if permission in selected and permission not in base_permissions]
    disabled_permissions = [permission for permission in PERMISSION_KEYS if permission not in selected and permission in base_permissions]
    profil, _ = ProfilUtilisateur.objects.get_or_create(user=user)
    profil.enabled_permissions = enabled_permissions
    profil.disabled_permissions = disabled_permissions
    profil.readonly_permissions = [
        permission
        for permission in READONLY_PERMISSION_KEYS
        if permission in readonly and permission in selected
    ]
    profil.save(update_fields=["enabled_permissions", "disabled_permissions", "readonly_permissions", "updated_at"])


def _validate_profile_photo(photo):
    if not photo:
        return photo
    allowed_content_types = {"image/jpeg", "image/png", "image/webp"}
    allowed_extensions = {"jpg", "jpeg", "png", "webp"}
    content_type = getattr(photo, "content_type", "")
    extension = photo.name.rsplit(".", 1)[-1].lower() if "." in photo.name else ""
    if content_type and content_type not in allowed_content_types:
        raise ValidationError("La photo doit etre au format JPG, PNG ou WebP.")
    if extension not in allowed_extensions:
        raise ValidationError("La photo doit etre au format JPG, PNG ou WebP.")
    if photo.size > 2 * 1024 * 1024:
        raise ValidationError("La photo ne doit pas depasser 2 Mo.")
    return photo


class PermissionSelectionMixin:
    def _selected_permission_keys(self):
        if self.is_bound:
            return set(self.data.getlist(self.add_prefix("action_permissions")))
        return set(self.fields["action_permissions"].initial or [])

    def _readonly_permission_keys(self):
        if self.is_bound:
            return set(self.data.getlist(self.add_prefix("readonly_permissions")))
        return set(self.fields["readonly_permissions"].initial or [])

    def _base_permission_keys(self):
        if self.is_bound:
            role_name = self.data.get(self.add_prefix("role")) or ""
            is_superuser = bool(self.data.get(self.add_prefix("is_superuser")))
        else:
            role_name = self.fields["role"].initial or ""
            is_superuser = bool(self.fields["is_superuser"].initial)
        return set(permission_keys_for_role(role_name, is_superuser=is_superuser))

    def permission_groups(self):
        selected = self._selected_permission_keys()
        readonly = self._readonly_permission_keys()
        base_permissions = self._base_permission_keys()
        groups = []
        for category, permissions in PERMISSION_CATALOG:
            active_count = sum(1 for key, _ in permissions if key in selected)
            groups.append(
                {
                    "label": category,
                    "active_count": active_count,
                    "total_count": len(permissions),
                    "permissions": [
                        {
                            "key": key,
                            "label": label,
                            "selected": key in selected,
                            "readonly": key in readonly,
                            "readonlyable": key in READONLY_PERMISSION_KEYS,
                            "base": key in base_permissions,
                        }
                        for key, label in permissions
                    ],
                }
            )
        return groups

    @property
    def selected_permissions_count(self):
        return len(self._selected_permission_keys())

    @property
    def permissions_count(self):
        return len(PERMISSION_KEYS)

    @property
    def role_permission_defaults(self):
        return {
            role_name: permission_keys_for_role(role_name)
            for role_name, _ in ROLE_CHOICES
        }


class UtilisateurCreationForm(PermissionSelectionMixin, forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    is_superuser = forms.BooleanField(label="Administrateur", required=False)
    action_permissions = forms.MultipleChoiceField(
        label="Actions autorisees",
        choices=_permission_choices(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    readonly_permissions = forms.MultipleChoiceField(
        label="Acces lecture seule",
        choices=[(key, key) for key in READONLY_PERMISSION_KEYS],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    password1 = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmation du mot de passe", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        ensure_role_groups()
        super().__init__(*args, **kwargs)
        self.fields["role"].initial = ROLE_CHOICES[0][0]
        self.fields["action_permissions"].initial = permission_keys_for_role(ROLE_CHOICES[0][0])
        self.fields["readonly_permissions"].initial = []

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
            _save_user_permission_overrides(
                user,
                self.cleaned_data.get("action_permissions"),
                self.cleaned_data.get("readonly_permissions"),
                self.cleaned_data["role"],
                self.cleaned_data.get("is_superuser", False),
            )
        return user


class UtilisateurModificationForm(PermissionSelectionMixin, forms.ModelForm):
    role = forms.ChoiceField(choices=ROLE_CHOICES)
    is_superuser = forms.BooleanField(label="Administrateur", required=False)
    action_permissions = forms.MultipleChoiceField(
        label="Actions autorisees",
        choices=_permission_choices(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    readonly_permissions = forms.MultipleChoiceField(
        label="Acces lecture seule",
        choices=[(key, key) for key in READONLY_PERMISSION_KEYS],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    new_password1 = forms.CharField(label="Nouveau mot de passe", widget=forms.PasswordInput, required=False)
    new_password2 = forms.CharField(label="Confirmation du nouveau mot de passe", widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        ensure_role_groups()
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            profil, _ = ProfilUtilisateur.objects.get_or_create(user=self.instance)
            self.profil = profil
            self.fields["role"].initial = get_user_role(self.instance)
            self.fields["is_superuser"].initial = self.instance.is_superuser
            self.fields["action_permissions"].initial = [
                permission for permission in PERMISSION_KEYS if build_user_permissions(self.instance).get(permission)
            ]
            self.fields["readonly_permissions"].initial = [
                permission for permission in READONLY_PERMISSION_KEYS if permission in set(profil.readonly_permissions or [])
            ]

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
            _save_user_permission_overrides(
                user,
                self.cleaned_data.get("action_permissions"),
                self.cleaned_data.get("readonly_permissions"),
                self.cleaned_data["role"],
                self.cleaned_data.get("is_superuser", False),
            )
        return user


class ProfilUtilisateurForm(forms.ModelForm):
    photo = forms.FileField(label="Photo de profil", required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "photo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            profil, _ = ProfilUtilisateur.objects.get_or_create(user=self.instance)
            self.profil = profil
            self.fields["photo"].initial = profil.photo
        else:
            self.profil = None

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        queryset = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Ce nom d'utilisateur existe deja.")
        return username

    def clean_photo(self):
        return _validate_profile_photo(self.cleaned_data.get("photo"))

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = (self.cleaned_data["username"] or "").strip()
        user.email = (self.cleaned_data.get("email") or "").strip()
        if commit:
            user.save()
            profil = self.profil or ProfilUtilisateur.objects.create(user=user)
            photo = self.cleaned_data.get("photo")
            if photo:
                profil.photo = photo
                profil.save()
        return user


class MotDePasseUtilisateurForm(PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": "Votre mot de passe actuel est incorrect.",
        "password_mismatch": "Les deux nouveaux mots de passe ne correspondent pas.",
    }
