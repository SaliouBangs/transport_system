from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .forms import UtilisateurCreationForm, UtilisateurModificationForm
from .permissions import (
    ensure_role_groups,
    get_default_landing_url,
    get_user_role_label,
    is_admin_or_directeur,
)


def home_redirect(request):
    if not request.user.is_authenticated:
        return redirect("connexion")
    return redirect(get_default_landing_url(request.user))


def connexion_view(request):
    if request.user.is_authenticated:
        return redirect(get_default_landing_url(request.user))

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_active:
            login(request, user)
            next_url = request.GET.get("next") or get_default_landing_url(user)
            return redirect(next_url)
        messages.error(request, "Nom d'utilisateur ou mot de passe invalide.")

    return render(request, "utilisateurs/connexion.html")


def deconnexion_view(request):
    logout(request)
    return redirect("connexion")


def liste_utilisateurs(request):
    if not is_admin_or_directeur(request.user):
        messages.error(request, "Seuls l'administrateur et le directeur peuvent gerer les comptes.")
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
    if not is_admin_or_directeur(request.user):
        messages.error(request, "Seuls l'administrateur et le directeur peuvent gerer les comptes.")
        return redirect(get_default_landing_url(request.user))

    if request.method == "POST":
        form = UtilisateurCreationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Le compte utilisateur a ete cree.")
            return redirect("utilisateurs")
    else:
        form = UtilisateurCreationForm()

    return render(request, "utilisateurs/ajouter_utilisateur.html", {"form": form})


def modifier_utilisateur(request, id):
    if not is_admin_or_directeur(request.user):
        messages.error(request, "Seuls l'administrateur et le directeur peuvent gerer les comptes.")
        return redirect(get_default_landing_url(request.user))

    utilisateur = get_object_or_404(User, id=id)
    if request.method == "POST":
        form = UtilisateurModificationForm(request.POST, instance=utilisateur)
        if form.is_valid():
            form.save()
            messages.success(request, "Le compte utilisateur a ete mis a jour.")
            return redirect("utilisateurs")
    else:
        form = UtilisateurModificationForm(instance=utilisateur)

    return render(
        request,
        "utilisateurs/modifier_utilisateur.html",
        {"form": form, "utilisateur": utilisateur},
    )
