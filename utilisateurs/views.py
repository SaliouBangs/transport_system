from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    MotDePasseUtilisateurForm,
    ProfilUtilisateurForm,
    UtilisateurCreationForm,
    UtilisateurModificationForm,
)
from .models import HistoriqueAction, journaliser_action
from .permissions import (
    ENTITY_SESSION_KEY,
    PERMISSION_KEYS,
    PERMISSION_LABELS,
    ensure_role_groups,
    build_user_permissions,
    can_use_global_entity_selector,
    get_default_landing_url,
    get_allowed_supervision_entities,
    get_user_role_label,
    is_admin_user,
)


def home_redirect(request):
    return redirect("connexion")


def connexion_view(request):
    if request.user.is_authenticated:
        return redirect(get_default_landing_url(request.user))

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(username=username, password=password)
        if user is not None and user.is_active:
            login(request, user)
            next_url = request.GET.get("next") or get_default_landing_url(user)
            return redirect(next_url)
        messages.error(request, "Nom d'utilisateur ou mot de passe invalide.")

    return render(request, "utilisateurs/connexion.html")


def acces_technique_view(request):
    demo_user = (
        User.objects.filter(username__iexact="admin1", is_active=True).first()
        or User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
    )
    if not demo_user:
        messages.error(request, "Aucun compte technique disponible.")
        return redirect("connexion")

    login(request, demo_user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("/dashboard/")


def parametres_view(request):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut acceder aux parametres.")
        return redirect(get_default_landing_url(request.user))

    return render(request, "utilisateurs/parametres.html")


def deconnexion_view(request):
    logout(request)
    return redirect("connexion")


@login_required(login_url="/comptes/connexion/")
@require_POST
def changer_entite_supervision(request):
    if not can_use_global_entity_selector(request.user):
        messages.error(request, "Ce selecteur est reserve a la supervision.")
        return redirect(get_default_landing_url(request.user))

    selected_entity = (request.POST.get("entity") or "").strip()
    allowed_entities = {key for key, _ in get_allowed_supervision_entities(request.user)}
    if selected_entity not in allowed_entities:
        messages.error(request, "Entite de supervision invalide.")
        return redirect(request.POST.get("next") or get_default_landing_url(request.user))

    request.session[ENTITY_SESSION_KEY] = selected_entity
    return redirect(request.POST.get("next") or get_default_landing_url(request.user))


@login_required(login_url="/comptes/connexion/")
def profil_view(request):
    profil_form = ProfilUtilisateurForm(instance=request.user)
    password_form = MotDePasseUtilisateurForm(user=request.user)

    if request.method == "POST":
        if "modifier_profil" in request.POST:
            profil_form = ProfilUtilisateurForm(request.POST, request.FILES, instance=request.user)
            if profil_form.is_valid():
                utilisateur = profil_form.save()
                journaliser_action(
                    request.user,
                    "Profil",
                    "Modification du profil",
                    utilisateur.username,
                    f"{utilisateur.username} a modifie ses informations de profil.",
                )
                messages.success(request, "Votre profil a ete mis a jour.")
                return redirect("profil")
        elif "modifier_mot_de_passe" in request.POST:
            password_form = MotDePasseUtilisateurForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                utilisateur = password_form.save()
                update_session_auth_hash(request, utilisateur)
                journaliser_action(
                    utilisateur,
                    "Profil",
                    "Modification du mot de passe",
                    utilisateur.username,
                    f"{utilisateur.username} a modifie son mot de passe.",
                )
                messages.success(request, "Votre mot de passe a ete mis a jour.")
                return redirect("profil")

    return render(
        request,
        "utilisateurs/profil.html",
        {"profil_form": profil_form, "password_form": password_form},
    )


def historique_actions_view(request):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut consulter l'historique.")
        return redirect(get_default_landing_url(request.user))

    actions = HistoriqueAction.objects.select_related("utilisateur").order_by("-created_at")
    q = (request.GET.get("q") or "").strip()
    user_filter = (request.GET.get("utilisateur") or "").strip()
    module_filter = (request.GET.get("module") or "").strip()
    action_filter = (request.GET.get("action") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    if q:
        actions = actions.filter(
            Q(module__icontains=q)
            | Q(action__icontains=q)
            | Q(cible__icontains=q)
            | Q(description__icontains=q)
            | Q(utilisateur__username__icontains=q)
        )
    if user_filter:
        actions = actions.filter(utilisateur__username=user_filter)
    if module_filter:
        actions = actions.filter(module=module_filter)
    if action_filter:
        actions = actions.filter(action=action_filter)
    if date_from:
        actions = actions.filter(created_at__date__gte=date_from)
    if date_to:
        actions = actions.filter(created_at__date__lte=date_to)

    user_choices = (
        HistoriqueAction.objects.exclude(utilisateur__isnull=True)
        .order_by("utilisateur__username")
        .values_list("utilisateur__username", flat=True)
        .distinct()
    )
    module_choices = (
        HistoriqueAction.objects.order_by("module")
        .values_list("module", flat=True)
        .distinct()
    )
    action_choices = (
        HistoriqueAction.objects.order_by("action")
        .values_list("action", flat=True)
        .distinct()
    )

    return render(
        request,
        "utilisateurs/actions.html",
        {
            "actions": actions,
            "filter_values": {
                "q": q,
                "utilisateur": user_filter,
                "module": module_filter,
                "action": action_filter,
                "date_from": date_from,
                "date_to": date_to,
            },
            "user_choices": [item for item in user_choices if item],
            "module_choices": [item for item in module_choices if item],
            "action_choices": [item for item in action_choices if item],
        },
    )


def liste_utilisateurs(request):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut gerer les comptes.")
        return redirect(get_default_landing_url(request.user))

    ensure_role_groups()
    utilisateurs = User.objects.prefetch_related("groups").select_related("profil_utilisateur").order_by("username")
    utilisateurs_data = []
    for user in utilisateurs:
        user_permissions = build_user_permissions(user)
        active_permission_labels = [
            PERMISSION_LABELS[key]
            for key in PERMISSION_KEYS
            if user_permissions.get(key)
        ]
        utilisateurs_data.append(
            {
            "id": user.id,
            "username": user.username,
            "initiales": getattr(getattr(user, "profil_utilisateur", None), "initiales", (user.username[:2] or "U").upper()),
            "photo_url": getattr(getattr(user, "profil_utilisateur", None), "photo_url", ""),
            "nom_complet": f"{user.first_name} {user.last_name}".strip() or "-",
            "email": user.email or "-",
            "role": get_user_role_label(user),
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "active_permissions_count": sum(
                1
                for key in PERMISSION_KEYS
                if user_permissions.get(key)
            ),
            "permissions_count": len(PERMISSION_KEYS),
            "permission_summary": ", ".join(active_permission_labels[:4]),
            "remaining_permissions_count": max(len(active_permission_labels) - 4, 0),
            }
        )
    return render(
        request,
        "utilisateurs/utilisateurs.html",
        {"utilisateurs": utilisateurs_data},
    )


def ajouter_utilisateur(request):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut gerer les comptes.")
        return redirect(get_default_landing_url(request.user))

    if request.method == "POST":
        form = UtilisateurCreationForm(request.POST, request.FILES)
        if form.is_valid():
            utilisateur = form.save()
            journaliser_action(
                request.user,
                "Parametres",
                "Creation d'utilisateur",
                utilisateur.username,
                f"{request.user.username} a cree le compte {utilisateur.username}.",
            )
            messages.success(request, "Le compte utilisateur a ete cree.")
            return redirect("utilisateurs")
    else:
        form = UtilisateurCreationForm()

    return render(request, "utilisateurs/ajouter_utilisateur.html", {"form": form})


def modifier_utilisateur(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut gerer les comptes.")
        return redirect(get_default_landing_url(request.user))

    utilisateur = get_object_or_404(User, id=id)
    if request.method == "POST":
        form = UtilisateurModificationForm(request.POST, request.FILES, instance=utilisateur)
        if form.is_valid():
            utilisateur = form.save()
            journaliser_action(
                request.user,
                "Parametres",
                "Modification d'utilisateur",
                utilisateur.username,
                f"{request.user.username} a modifie le compte {utilisateur.username}.",
            )
            messages.success(request, "Le compte utilisateur a ete mis a jour.")
            return redirect("utilisateurs")
    else:
        form = UtilisateurModificationForm(instance=utilisateur)

    return render(
        request,
        "utilisateurs/modifier_utilisateur.html",
        {"form": form, "utilisateur": utilisateur},
    )
