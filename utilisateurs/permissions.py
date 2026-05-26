from functools import wraps

from django.contrib import messages
from django.contrib.auth.models import Group
from django.shortcuts import redirect

from .constants import (
    ROLE_CAISSIERE,
    ROLE_CAISSIERE_AVENA,
    ROLE_CAISSIERE_SONI,
    ROLE_CHEF_CHAUFFEUR,
    ROLE_CHOICES,
    ROLE_COMMERCIAL,
    ROLE_COMPTABLE,
    ROLE_COMPTABLE_AVENA,
    ROLE_COMPTABLE_SOGEFI,
    ROLE_CONTROLEUR,
    ROLE_DGA,
    ROLE_DGA_AVENA,
    ROLE_DGA_SOGEFI,
    ROLE_DIRECTEUR,
    ROLE_INVITE,
    ROLE_LABELS,
    ROLE_LOGISTIQUE,
    ROLE_MAINTENANCIER,
    ROLE_RESPONSABLE_ACHAT,
    ROLE_RESPONSABLE_COMMERCIAL,
    ROLE_SECRETAIRE,
    ROLE_TRANSITAIRE,
)


ROLE_NAMES = [role for role, _ in ROLE_CHOICES]
ENTITY_ALL = "all"
ENTITY_SONI = "soni"
ENTITY_SOGEFI = "sogefi"
ENTITY_AVENA = "avena"
ENTITY_CHOICES = [
    (ENTITY_ALL, "Toutes"),
    (ENTITY_SONI, "SONI"),
    (ENTITY_SOGEFI, "SOGEFI"),
    (ENTITY_AVENA, "Avena"),
]
ENTITY_LABELS = dict(ENTITY_CHOICES)
ENTITY_SESSION_KEY = "supervision_entity"
SUPERVISION_ENTITY_ROLES = {ROLE_DIRECTEUR, ROLE_CONTROLEUR}
PERMISSION_CATALOG = [
    (
        "Navigation generale",
        [
            ("can_access_dashboard", "Voir le tableau de bord"),
            ("can_access_settings", "Ouvrir les parametres"),
            ("can_manage_users", "Gerer les comptes utilisateurs"),
            ("can_access_gps", "Acceder au GPS"),
            ("can_access_rapport_global", "Consulter le rapport global"),
            ("can_access_reports_center", "Consulter les rapports"),
        ],
    ),
    (
        "Commercial",
        [
            ("can_access_prospects", "Voir les prospects"),
            ("can_add_prospects", "Creer des prospects"),
            ("can_delete_prospects", "Supprimer des prospects"),
            ("can_convert_prospects", "Convertir un prospect"),
            ("can_access_clients", "Voir les clients"),
            ("can_add_clients", "Creer des clients"),
            ("can_edit_clients", "Modifier des clients"),
            ("can_delete_clients", "Supprimer des clients"),
            ("can_manage_client_portfolios", "Gerer les portefeuilles clients"),
            ("can_access_commandes", "Voir les commandes"),
            ("can_add_commandes", "Creer des commandes"),
            ("can_edit_commandes", "Modifier des commandes"),
        ],
    ),
    (
        "Operations BL",
        [
            ("can_access_operations_general", "Vue generale operations"),
            ("can_access_operations_comptable", "Operation comptable"),
            ("can_access_operations_secretaire", "Secretaire BL"),
            ("can_access_operations_sommiers", "Sommiers"),
            ("can_access_operations_facturation", "Facturation"),
            ("can_access_operations_logistique", "Commandes a affecter"),
            ("can_access_operations_logisticien", "Chargement / Livraison"),
            ("can_access_operations_chef_chauffeur", "Chef chauffeur"),
            ("can_access_operations_transitaire", "Transitaire"),
        ],
    ),
    (
        "Logistique et parc",
        [
            ("can_access_camions", "Voir le parc automobile"),
            ("can_access_chauffeurs", "Voir les chauffeurs"),
            ("can_access_documents", "Voir les documents"),
            ("can_manage_logistique_assets", "Gerer les ressources logistiques"),
        ],
    ),
    (
        "Maintenance, caisse et depenses",
        [
            ("can_access_maintenance", "Acceder a la maintenance / caisse"),
            ("can_access_maintenance_payment", "Traiter les paiements maintenance"),
            ("can_access_depenses", "Acceder aux depenses"),
            ("can_access_depenses_expression_validation", "Valider les expressions de besoin"),
            ("can_access_depenses_engagement", "Engager les depenses"),
            ("can_access_depenses_payment_cheque", "Payer les depenses par cheque"),
            ("can_access_depenses_payment_espece", "Payer les depenses en espece"),
        ],
    ),
]
PERMISSION_LABELS = {
    permission_key: label
    for _, permissions in PERMISSION_CATALOG
    for permission_key, label in permissions
}
PERMISSION_KEYS = list(PERMISSION_LABELS.keys())
READONLY_PERMISSION_KEYS = [
    key
    for key in PERMISSION_KEYS
    if key.startswith("can_access_")
    or key in {"can_manage_users", "can_manage_logistique_assets", "can_manage_client_portfolios"}
]
ROLE_PRIORITY = [
    ROLE_DGA_SOGEFI,
    ROLE_DGA_AVENA,
    ROLE_COMPTABLE_SOGEFI,
    ROLE_COMPTABLE_AVENA,
    ROLE_CAISSIERE_SONI,
    ROLE_CAISSIERE_AVENA,
    ROLE_DGA,
    ROLE_COMPTABLE,
    ROLE_CAISSIERE,
    *[
        role
        for role in ROLE_NAMES
        if role
        not in {
            ROLE_DGA_SOGEFI,
            ROLE_DGA_AVENA,
            ROLE_COMPTABLE_SOGEFI,
            ROLE_COMPTABLE_AVENA,
            ROLE_CAISSIERE_SONI,
            ROLE_CAISSIERE_AVENA,
            ROLE_DGA,
            ROLE_COMPTABLE,
            ROLE_CAISSIERE,
        }
    ],
]


def ensure_role_groups():
    for role_name in ROLE_NAMES:
        Group.objects.get_or_create(name=role_name)


def get_user_role(user):
    if not getattr(user, "is_authenticated", False):
        return ""

    user_group_names = list(user.groups.values_list("name", flat=True))
    for role_name in ROLE_PRIORITY:
        if role_name in user_group_names:
            return role_name
    return ""


def get_user_role_label(user):
    if getattr(user, "is_superuser", False):
        return "Administrateur"
    role_name = get_user_role(user)
    if not role_name:
        return "Administrateur" if getattr(user, "is_superuser", False) else "Aucun role"
    return ROLE_LABELS.get(role_name, role_name.title())


def is_admin_user(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


def can_use_global_entity_selector(user):
    return bool(is_admin_user(user) or get_user_role(user) in SUPERVISION_ENTITY_ROLES)


def get_allowed_supervision_entities(user):
    if not getattr(user, "is_authenticated", False):
        return []
    if can_use_global_entity_selector(user):
        return ENTITY_CHOICES
    role = get_user_role(user)
    if role in {ROLE_COMPTABLE_AVENA, ROLE_CAISSIERE_AVENA, ROLE_DGA_AVENA}:
        return [(ENTITY_AVENA, ENTITY_LABELS[ENTITY_AVENA])]
    if role in {ROLE_COMPTABLE_SOGEFI, ROLE_DGA_SOGEFI}:
        return [(ENTITY_SOGEFI, ENTITY_LABELS[ENTITY_SOGEFI])]
    if role in {ROLE_COMPTABLE, ROLE_CAISSIERE, ROLE_CAISSIERE_SONI, ROLE_DGA, ROLE_LOGISTIQUE}:
        return [(ENTITY_SONI, ENTITY_LABELS[ENTITY_SONI])]
    return []


def get_active_supervision_entity(request):
    user = getattr(request, "user", None)
    allowed_entities = get_allowed_supervision_entities(user)
    allowed_keys = {key for key, _ in allowed_entities}
    if not allowed_keys:
        return ""
    if can_use_global_entity_selector(user):
        selected = request.session.get(ENTITY_SESSION_KEY, ENTITY_ALL)
        return selected if selected in allowed_keys else ENTITY_ALL
    return next(iter(allowed_keys))


def get_active_supervision_entity_label(request):
    return ENTITY_LABELS.get(get_active_supervision_entity(request), "")


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
    role = get_user_role(user)
    if role == ROLE_SECRETAIRE:
        return "/operations/secretaire/"
    if role == ROLE_CHEF_CHAUFFEUR:
        return "/operations/chef-chauffeur/"
    return "/dashboard/"


PATH_PERMISSION_MAP = [
    ("/dashboard/", "can_access_dashboard"),
    ("/prospects/", "can_access_prospects"),
    ("/clients/encaissements/", "can_access_clients"),
    ("/clients/", "can_access_clients"),
    ("/commandes/rapport-global/", "can_access_rapport_global"),
    ("/commandes/", "can_access_commandes"),
    ("/operations/comptable/sommiers/", "can_access_operations_sommiers"),
    ("/operations/comptable/", "can_access_operations_comptable"),
    ("/operations/secretaire/", "can_access_operations_secretaire"),
    ("/operations/facturation/", "can_access_operations_facturation"),
    ("/operations/logisticien/", "can_access_operations_logisticien"),
    ("/operations/chef-chauffeur/", "can_access_operations_chef_chauffeur"),
    ("/operations/transitaire/", "can_access_operations_transitaire"),
    ("/operations/", "can_access_operations_general"),
    ("/camions/rapports/", "can_access_reports_center"),
    ("/camions/", "can_access_camions"),
    ("/chauffeurs/", "can_access_chauffeurs"),
    ("/documents/", "can_access_documents"),
    ("/maintenance/paiements/", "can_access_maintenance_payment"),
    ("/maintenance/", "can_access_maintenance"),
    ("/depenses/paiement/", "can_access_maintenance_payment"),
    ("/depenses/", "can_access_depenses"),
]


def get_path_permission_key(path):
    normalized_path = path or ""
    for prefix, permission_key in PATH_PERMISSION_MAP:
        if normalized_path.startswith(prefix):
            return permission_key
    return ""


def is_readonly_request(user, path, method):
    if method in {"GET", "HEAD", "OPTIONS", "TRACE"} or is_admin_user(user):
        return False
    permission_key = get_path_permission_key(path)
    if not permission_key:
        return False
    try:
        profil = user.profil_utilisateur
    except Exception:
        return False
    return permission_key in set(profil.readonly_permissions or [])


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect(f"/comptes/connexion/?next={request.path}")

            permission_key = get_path_permission_key(request.path)
            permissions = build_user_permissions(user)
            has_role_access = is_admin_user(user) or get_user_role(user) in roles
            has_permission_access = bool(permission_key and permissions.get(permission_key))

            if has_role_access or has_permission_access:
                if permission_key and not permissions.get(permission_key, True):
                    messages.error(request, "Cette action a ete desactivee pour votre compte.")
                    return redirect(get_default_landing_url(user))
                if is_readonly_request(user, request.path, request.method):
                    messages.error(request, "Votre acces a cette page est en lecture seule.")
                    return redirect(request.META.get("HTTP_REFERER") or get_default_landing_url(user))
                return view_func(request, *args, **kwargs)

            messages.error(request, "Vous n'avez pas acces a cette page.")
            return redirect(get_default_landing_url(user))

        return wrapped_view

    return decorator


def _apply_user_permission_overrides(user, permissions):
    if is_admin_user(user) or not getattr(user, "is_authenticated", False):
        return permissions
    try:
        profil = user.profil_utilisateur
    except Exception:
        return permissions

    enabled_permissions = set(profil.enabled_permissions or [])
    for permission_key in enabled_permissions:
        if permission_key in permissions and permission_key in PERMISSION_LABELS:
            permissions[permission_key] = True

    disabled_permissions = set(profil.disabled_permissions or [])
    for permission_key in disabled_permissions:
        if permission_key in permissions and permission_key in PERMISSION_LABELS:
            permissions[permission_key] = False
    return permissions


def _role_has(role, *roles, is_boss=False):
    return bool(is_boss or role in roles)


def build_role_permissions(role, is_superuser=False, is_authenticated=True):
    is_boss = bool(is_superuser)
    is_directeur_role = role == ROLE_DIRECTEUR
    is_caissiere = role in {ROLE_CAISSIERE, ROLE_CAISSIERE_SONI, ROLE_CAISSIERE_AVENA}
    return {
        "user_role": role,
        "user_role_label": "Administrateur" if is_boss else ROLE_LABELS.get(role, role.title() if role else "Aucun role"),
        "is_admin_user": is_boss,
        "is_admin_or_directeur": is_boss or is_directeur_role,
        "can_manage_users": is_boss or is_directeur_role,
        "can_access_settings": is_boss or is_directeur_role,
        "can_access_dashboard": bool(is_authenticated),
        "can_access_gps": _role_has(role, ROLE_COMPTABLE, ROLE_LOGISTIQUE, ROLE_TRANSITAIRE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_prospects": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_add_prospects": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_delete_prospects": is_boss or role == ROLE_DIRECTEUR,
        "can_convert_prospects": is_boss or is_directeur_role,
        "can_access_clients": bool(is_authenticated and not is_caissiere),
        "can_add_clients": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_edit_clients": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_delete_clients": is_boss or role == ROLE_DIRECTEUR,
        "can_manage_client_portfolios": _role_has(role, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_commandes": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_COMPTABLE, ROLE_DGA, ROLE_DIRECTEUR, ROLE_LOGISTIQUE, is_boss=is_boss),
        "can_add_commandes": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_edit_commandes": _role_has(role, ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_general": is_boss or is_directeur_role,
        "can_access_operations_comptable": _role_has(role, ROLE_COMPTABLE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_secretaire": _role_has(role, ROLE_SECRETAIRE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_sommiers": _role_has(role, ROLE_COMPTABLE, ROLE_DGA, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_facturation": _role_has(role, ROLE_COMPTABLE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_logistique": _role_has(role, ROLE_LOGISTIQUE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_logisticien": _role_has(role, ROLE_LOGISTIQUE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_chef_chauffeur": _role_has(role, ROLE_CHEF_CHAUFFEUR, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_operations_transitaire": _role_has(role, ROLE_TRANSITAIRE, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_camions": _role_has(role, ROLE_LOGISTIQUE, ROLE_MAINTENANCIER, ROLE_DGA, ROLE_DIRECTEUR, ROLE_INVITE, ROLE_CONTROLEUR, is_boss=is_boss),
        "can_access_chauffeurs": _role_has(role, ROLE_LOGISTIQUE, ROLE_MAINTENANCIER, ROLE_DGA, ROLE_DIRECTEUR, ROLE_INVITE, ROLE_CONTROLEUR, is_boss=is_boss),
        "can_access_documents": _role_has(role, ROLE_LOGISTIQUE, ROLE_MAINTENANCIER, ROLE_DGA, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_maintenance": _role_has(
            role,
            ROLE_COMPTABLE,
            ROLE_COMPTABLE_AVENA,
            ROLE_CAISSIERE,
            ROLE_CAISSIERE_AVENA,
            ROLE_CAISSIERE_SONI,
            ROLE_CONTROLEUR,
            ROLE_LOGISTIQUE,
            ROLE_MAINTENANCIER,
            ROLE_DGA,
            ROLE_DGA_AVENA,
            ROLE_DIRECTEUR,
            ROLE_INVITE,
            is_boss=is_boss,
        ),
        "can_access_maintenance_payment": _role_has(
            role,
            ROLE_COMPTABLE,
            ROLE_COMPTABLE_AVENA,
            ROLE_CAISSIERE,
            ROLE_CAISSIERE_AVENA,
            ROLE_CAISSIERE_SONI,
            ROLE_DIRECTEUR,
            is_boss=is_boss,
        ),
        "can_access_depenses": bool(is_authenticated and not is_caissiere),
        "can_access_rapport_global": bool(is_authenticated and not is_caissiere),
        "can_access_reports_center": bool(is_authenticated and not is_caissiere),
        "can_manage_logistique_assets": _role_has(
            role,
            ROLE_LOGISTIQUE,
            ROLE_MAINTENANCIER,
            ROLE_DGA,
            ROLE_DIRECTEUR,
            is_boss=is_boss,
        ),
        "can_access_depenses_expression_validation": _role_has(role, ROLE_DGA_SOGEFI, ROLE_DGA_AVENA, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_depenses_engagement": _role_has(role, ROLE_RESPONSABLE_ACHAT, ROLE_COMPTABLE_AVENA, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_depenses_payment_cheque": _role_has(role, ROLE_COMPTABLE_SOGEFI, ROLE_COMPTABLE_AVENA, ROLE_DIRECTEUR, is_boss=is_boss),
        "can_access_depenses_payment_espece": _role_has(role, ROLE_CAISSIERE, ROLE_CAISSIERE_SONI, ROLE_CAISSIERE_AVENA, ROLE_DIRECTEUR, is_boss=is_boss),
    }


def permission_keys_for_role(role, is_superuser=False):
    role_permissions = build_role_permissions(role, is_superuser=is_superuser)
    return [key for key in PERMISSION_KEYS if role_permissions.get(key)]


def build_user_permissions(user):
    permissions = build_role_permissions(
        get_user_role(user),
        is_superuser=is_admin_user(user),
        is_authenticated=bool(getattr(user, "is_authenticated", False)),
    )
    return _apply_user_permission_overrides(user, permissions)
