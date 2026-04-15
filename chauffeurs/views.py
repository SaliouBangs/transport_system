from django.shortcuts import get_object_or_404, redirect, render
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import role_required

from .forms import ChauffeurForm
from .models import Chauffeur


def liste_chauffeurs(request):
    chauffeurs = Chauffeur.objects.select_related("camion")
    return render(request, "chauffeurs/chauffeurs.html", {"chauffeurs": chauffeurs})


def ajouter_chauffeur(request):
    if request.method == "POST":
        form = ChauffeurForm(request.POST)
        if form.is_valid():
            chauffeur = form.save()
            journaliser_action(
                request.user,
                "Chauffeurs",
                "Ajout de chauffeur",
                chauffeur.nom,
                f"{request.user.username} a ajoute le chauffeur {chauffeur.nom}.",
            )
            return redirect("chauffeurs")
    else:
        form = ChauffeurForm()

    return render(request, "chauffeurs/ajouter_chauffeur.html", {"form": form})


def modifier_chauffeur(request, id):
    chauffeur = get_object_or_404(Chauffeur, id=id)
    if request.method == "POST":
        form = ChauffeurForm(request.POST, instance=chauffeur)
        if form.is_valid():
            chauffeur = form.save()
            journaliser_action(
                request.user,
                "Chauffeurs",
                "Modification de chauffeur",
                chauffeur.nom,
                f"{request.user.username} a modifie le chauffeur {chauffeur.nom}.",
            )
            return redirect("chauffeurs")
    else:
        form = ChauffeurForm(instance=chauffeur)

    return render(
        request,
        "chauffeurs/modifier_chauffeur.html",
        {"form": form, "chauffeur": chauffeur},
    )


def supprimer_chauffeur(request, id):
    chauffeur = get_object_or_404(Chauffeur, id=id)
    chauffeur_label = chauffeur.nom
    chauffeur.delete()
    journaliser_action(
        request.user,
        "Chauffeurs",
        "Suppression de chauffeur",
        chauffeur_label,
        f"{request.user.username} a supprime le chauffeur {chauffeur_label}.",
    )
    return redirect("chauffeurs")


liste_chauffeurs = role_required("logistique", "maintenancier", "directeur")(liste_chauffeurs)
ajouter_chauffeur = role_required("logistique", "maintenancier", "directeur")(ajouter_chauffeur)
modifier_chauffeur = role_required("logistique", "maintenancier", "directeur")(modifier_chauffeur)
supprimer_chauffeur = role_required("logistique", "maintenancier", "directeur")(supprimer_chauffeur)
