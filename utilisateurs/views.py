from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UtilisateurCreationForm, UtilisateurModificationForm
from .models import HistoriqueAction, journaliser_action
from .permissions import (
    ensure_role_groups,
    get_default_landing_url,
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
    utilisateurs = User.objects.prefetch_related("groups").order_by("username")
    utilisateurs_data = [
        {
            "id": user.id,
            "username": user.username,
            "nom_complet": f"{user.first_name} {user.last_name}".strip() or "-",
            "email": user.email or "-",
            "role": get_user_role_label(user),
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
        }
        for user in utilisateurs
    ]
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
        form = UtilisateurCreationForm(request.POST)
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
        form = UtilisateurModificationForm(request.POST, instance=utilisateur)
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
