from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import Group
from django.shortcuts import redirect

from .constants import (
    ROLE_CHOICES,
    ROLE_COMMERCIAL,
    ROLE_COMPTABLE,
    ROLE_DIRECTEUR,
    ROLE_LABELS,
    ROLE_LOGISTIQUE,
    ROLE_MAINTENANCIER,
    ROLE_TRANSITAIRE,
)


ROLE_NAMES = [role for role, _ in ROLE_CHOICES]


def ensure_role_groups():
    for role_name in ROLE_NAMES:
        Group.objects.get_or_create(name=role_name)


def get_user_role(user):
    if not getattr(user, "is_authenticated", False):
        return ""

    user_group_names = list(user.groups.values_list("name", flat=True))
    for role_name in ROLE_NAMES:
        if role_name in user_group_names:
            return role_name
    return ""


def get_user_role_label(user):
    role_name = get_user_role(user)
    if not role_name:
        return "Administrateur" if getattr(user, "is_superuser", False) else "Aucun role"
    return ROLE_LABELS.get(role_name, role_name.title())


def is_admin_user(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


def is_directeur(user):
    return get_user_role(user) == ROLE_DIRECTEUR


def is_admin_or_directeur(user):
    return is_admin_user(user) or is_directeur(user)


def user_has_role(user, *roles):
    if is_admin_user(user):
        return True
    return get_user_role(user) in roles


def assign_role(user, role_name):
    ensure_role_groups()
    user.groups.remove(*user.groups.filter(name__in=ROLE_NAMES))
    if role_name:
        user.groups.add(Group.objects.get(name=role_name))


def get_default_landing_url(user):
    if not getattr(user, "is_authenticated", False):
        return "/comptes/connexion/"
    return "/dashboard/"


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"/comptes/connexion/?next={request.path}")

            if is_admin_user(user) or get_user_role(user) in roles:
                return view_func(request, *args, **kwargs)

            messages.error(request, "Vous n'avez pas acces a cette page.")
            return redirect(get_default_landing_url(user))

        return wrapped_view

    return decorator


def build_user_permissions(user):
    is_boss = is_admin_or_directeur(user)
    return {
        "user_role": get_user_role(user),
        "user_role_label": get_user_role_label(user),
        "is_admin_user": is_admin_user(user),
        "is_admin_or_directeur": is_boss,
        "can_manage_users": is_boss,
        "can_access_settings": is_boss,
        "can_access_dashboard": bool(getattr(user, "is_authenticated", False)),
        "can_access_gps": bool(getattr(user, "is_authenticated", False)) and get_user_role(user) != ROLE_MAINTENANCIER,
        "can_access_prospects": user_has_role(user, ROLE_COMMERCIAL, ROLE_DIRECTEUR),
        "can_add_prospects": user_has_role(user, ROLE_COMMERCIAL, ROLE_DIRECTEUR),
        "can_convert_prospects": is_boss,
        "can_access_clients": user_has_role(user, ROLE_COMMERCIAL, ROLE_DIRECTEUR),
        "can_add_clients": user_has_role(user, ROLE_COMMERCIAL, ROLE_DIRECTEUR),
        "can_edit_clients": is_boss,
        "can_access_commandes": user_has_role(user, ROLE_COMMERCIAL, ROLE_COMPTABLE, ROLE_DIRECTEUR),
        "can_add_commandes": user_has_role(user, ROLE_COMMERCIAL, ROLE_DIRECTEUR),
        "can_edit_commandes": is_boss,
        "can_access_operations_general": is_boss,
        "can_access_operations_comptable": user_has_role(user, ROLE_COMPTABLE, ROLE_DIRECTEUR),
        "can_access_operations_facturation": user_has_role(user, ROLE_COMPTABLE, ROLE_DIRECTEUR),
        "can_access_operations_logistique": user_has_role(user, ROLE_LOGISTIQUE, ROLE_DIRECTEUR),
        "can_access_operations_logisticien": user_has_role(user, ROLE_LOGISTIQUE, ROLE_DIRECTEUR),
        "can_access_operations_transitaire": user_has_role(user, ROLE_TRANSITAIRE, ROLE_DIRECTEUR),
        "can_manage_logistique_assets": user_has_role(
            user,
            ROLE_LOGISTIQUE,
            ROLE_MAINTENANCIER,
            ROLE_DIRECTEUR,
        ),
    }
