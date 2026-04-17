from django.http import JsonResponse
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import role_required

from .forms import CamionForm, TransporteurForm
from .models import Camion


def liste_camions(request):
    panne_threshold = 3
    camions = Camion.objects.select_related("transporteur").annotate(
        panne_count=Count("maintenances", distinct=True)
    )
    for camion in camions:
        if camion.panne_count:
            camion.performance_percent = round((camion.panne_count / panne_threshold) * 100, 1)
        else:
            camion.performance_percent = 0

        if camion.panne_count <= 1:
            camion.performance_label = "Excellent"
            camion.performance_variant = "ok"
        elif camion.panne_count == 2:
            camion.performance_label = "Bon"
            camion.performance_variant = "warning"
        elif camion.panne_count == 3:
            camion.performance_label = "Moyen"
            camion.performance_variant = "mid"
        else:
            camion.performance_label = "Mauvais"
            camion.performance_variant = "danger"
    return render(
        request,
        "camions/camions.html",
        {"camions": camions, "panne_threshold": panne_threshold},
    )


def ajouter_camion(request):
    if request.method == "POST":
        form = CamionForm(request.POST)
        if form.is_valid():
            camion = form.save()
            camion_label = camion.numero_tracteur or camion.numero_citerne or f"Camion #{camion.id}"
            journaliser_action(
                request.user,
                "Camions",
                "Ajout de camion",
                camion_label,
                f"{request.user.username} a ajoute le camion {camion_label}.",
            )
            return redirect("camions")
    else:
        form = CamionForm()

    return render(
        request,
        "camions/ajouter_camion.html",
        {"form": form, "transporteur_form": TransporteurForm()},
    )


def modifier_camion(request, id):
    camion = get_object_or_404(Camion, id=id)
    if request.method == "POST":
        form = CamionForm(request.POST, instance=camion)
        if form.is_valid():
            camion = form.save()
            camion_label = camion.numero_tracteur or camion.numero_citerne or f"Camion #{camion.id}"
            journaliser_action(
                request.user,
                "Camions",
                "Modification de camion",
                camion_label,
                f"{request.user.username} a modifie le camion {camion_label}.",
            )
            return redirect("camions")
    else:
        form = CamionForm(instance=camion)

    return render(
        request,
        "camions/modifier_camion.html",
        {"form": form, "transporteur_form": TransporteurForm()},
    )


def supprimer_camion(request, id):
    camion = get_object_or_404(Camion, id=id)
    camion_label = camion.numero_tracteur or camion.numero_citerne or f"Camion #{camion.id}"
    camion.delete()
    journaliser_action(
        request.user,
        "Camions",
        "Suppression de camion",
        camion_label,
        f"{request.user.username} a supprime le camion {camion_label}.",
    )
    return redirect("camions")


def ajouter_transporteur_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = TransporteurForm(request.POST)
    if form.is_valid():
        transporteur = form.save()
        return JsonResponse(
            {
                "success": True,
                "transporteur": {
                    "id": transporteur.id,
                    "label": transporteur.nom,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


liste_camions = role_required("logistique", "maintenancier", "dga", "directeur", "invite")(liste_camions)
ajouter_camion = role_required("logistique", "maintenancier", "dga", "directeur")(ajouter_camion)
modifier_camion = role_required("logistique", "maintenancier", "dga", "directeur")(modifier_camion)
supprimer_camion = role_required("logistique", "maintenancier", "dga", "directeur")(supprimer_camion)
ajouter_transporteur_modal = role_required("logistique", "maintenancier", "dga", "directeur")(ajouter_transporteur_modal)
