from django.shortcuts import get_object_or_404, redirect, render
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
            form.save()
            return redirect("chauffeurs")
    else:
        form = ChauffeurForm()

    return render(request, "chauffeurs/ajouter_chauffeur.html", {"form": form})


def modifier_chauffeur(request, id):
    chauffeur = get_object_or_404(Chauffeur, id=id)
    if request.method == "POST":
        form = ChauffeurForm(request.POST, instance=chauffeur)
        if form.is_valid():
            form.save()
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
    chauffeur.delete()
    return redirect("chauffeurs")


liste_chauffeurs = role_required("logistique", "directeur")(liste_chauffeurs)
ajouter_chauffeur = role_required("logistique", "directeur")(ajouter_chauffeur)
modifier_chauffeur = role_required("logistique", "directeur")(modifier_chauffeur)
supprimer_chauffeur = role_required("logistique", "directeur")(supprimer_chauffeur)
