from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import role_required

from .forms import ClientForm
from .models import Client


def liste_clients(request):
    clients = Client.objects.select_related("prospect")
    return render(request, "clients/clients.html", {"clients": clients})


def ajouter_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST)
        if form.is_valid():
            client = form.save()
            journaliser_action(
                request.user,
                "Clients",
                "Ajout de client",
                client.entreprise,
                f"{request.user.username} a ajoute le client {client.entreprise}.",
            )
            return redirect("clients")
    else:
        form = ClientForm()

    return render(request, "clients/ajouter_client.html", {"form": form})


def modifier_client(request, id):
    client = get_object_or_404(Client, id=id)
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client)
        if form.is_valid():
            client = form.save()
            journaliser_action(
                request.user,
                "Clients",
                "Modification de client",
                client.entreprise,
                f"{request.user.username} a modifie le client {client.entreprise}.",
            )
            return redirect("clients")
    else:
        form = ClientForm(instance=client)

    return render(
        request,
        "clients/modifier_client.html",
        {"form": form, "client": client},
    )


def supprimer_client(request, id):
    client = get_object_or_404(Client, id=id)
    client_label = client.entreprise
    client.delete()
    journaliser_action(
        request.user,
        "Clients",
        "Suppression de client",
        client_label,
        f"{request.user.username} a supprime le client {client_label}.",
    )
    return redirect("clients")


def prospect_infos(request):
    prospect_id = request.GET.get("prospect_id")
    if not prospect_id:
        return JsonResponse(
            {"success": False, "errors": {"prospect": ["Prospect manquant."]}},
            status=400,
        )

    from prospects.models import Prospect

    prospect = Prospect.objects.filter(id=prospect_id).first()
    if not prospect:
        return JsonResponse(
            {"success": False, "errors": {"prospect": ["Prospect introuvable."]}},
            status=404,
        )

    return JsonResponse(
        {
            "success": True,
            "prospect": {
                "nom": prospect.nom,
                "telephone": prospect.telephone,
                "entreprise": prospect.entreprise,
                "ville": prospect.ville,
            },
        }
    )


def ajouter_client_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = ClientForm(request.POST)
    if form.is_valid():
        client = form.save()
        journaliser_action(
            request.user,
            "Clients",
            "Ajout de client",
            client.entreprise,
            f"{request.user.username} a ajoute le client {client.entreprise} depuis une fenetre modale.",
        )
        return JsonResponse(
            {
                "success": True,
                "client": {
                    "id": client.id,
                    "label": client.entreprise,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


liste_clients = role_required("commercial", "directeur")(liste_clients)
ajouter_client = role_required("commercial", "directeur")(ajouter_client)
modifier_client = role_required("directeur")(modifier_client)
supprimer_client = role_required("directeur")(supprimer_client)
prospect_infos = role_required("commercial", "directeur")(prospect_infos)
ajouter_client_modal = role_required("commercial", "directeur")(ajouter_client_modal)
