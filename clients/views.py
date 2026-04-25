from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.models import User
from django.db.models import Q
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, role_required
from utilisateurs.constants import ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL

from .forms import ClientForm
from .models import Client


def liste_clients(request):
    clients = Client.objects.select_related("prospect", "commercial")
    if get_user_role(request.user) == "commercial":
        clients = clients.filter(commercial=request.user)
    return render(
        request,
        "clients/clients.html",
        {
            "clients": clients,
            "clients_non_affectes": Client.objects.filter(commercial__isnull=True).count(),
        },
    )


def portefeuille_clients(request):
    query = request.GET.get("q", "").strip()
    commercial_id = request.GET.get("commercial", "").strip()
    scope = request.GET.get("scope", "").strip() or "all"

    commerciaux = User.objects.filter(
        groups__name__in=[ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL]
    ).distinct().order_by("first_name", "last_name", "username")

    clients = Client.objects.select_related("prospect", "commercial").order_by("entreprise", "nom")
    if query:
        clients = clients.filter(
            Q(entreprise__icontains=query)
            | Q(nom__icontains=query)
            | Q(ville__icontains=query)
        )
    if commercial_id:
        clients = clients.filter(commercial_id=commercial_id)
    if scope == "unassigned":
        clients = clients.filter(commercial__isnull=True)

    if request.method == "POST":
        client_id = request.POST.get("client_id")
        target_commercial_id = request.POST.get("commercial_id")
        client = get_object_or_404(Client, id=client_id)
        commercial = get_object_or_404(commerciaux, id=target_commercial_id)
        client.commercial = commercial
        client.save(update_fields=["commercial"])
        journaliser_action(
            request.user,
            "Clients",
            "Affectation portefeuille",
            client.entreprise,
            (
                f"{request.user.username} a affecte le client {client.entreprise} "
                f"au portefeuille de {commercial.username}."
            ),
        )
        messages.success(
            request,
            f"Le client {client.entreprise} a ete affecte a {commercial.get_full_name() or commercial.username}.",
        )
        suffix = request.GET.urlencode()
        return redirect(f"/clients/portefeuilles/{'?' + suffix if suffix else ''}")

    return render(
        request,
        "clients/portefeuilles.html",
        {
            "clients": clients,
            "commerciaux": commerciaux,
            "query": query,
            "commercial_id": commercial_id,
            "scope": scope,
        },
    )


def ajouter_client(request):
    if request.method == "POST":
        form = ClientForm(request.POST, user=request.user)
        if form.is_valid():
            client = form.save(commit=False)
            if get_user_role(request.user) == "commercial":
                client.commercial = request.user
            client.save()
            journaliser_action(
                request.user,
                "Clients",
                "Ajout de client",
                client.entreprise,
                f"{request.user.username} a ajoute le client {client.entreprise}.",
            )
            return redirect("clients")
    else:
        form = ClientForm(user=request.user)

    return render(request, "clients/ajouter_client.html", {"form": form})


def modifier_client(request, id):
    client_queryset = Client.objects.all()
    if get_user_role(request.user) == "commercial":
        client_queryset = client_queryset.filter(commercial=request.user)
    client = get_object_or_404(client_queryset, id=id)
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client, user=request.user)
        if form.is_valid():
            client = form.save(commit=False)
            if get_user_role(request.user) == "commercial":
                client.commercial = request.user
            client.save()
            journaliser_action(
                request.user,
                "Clients",
                "Modification de client",
                client.entreprise,
                f"{request.user.username} a modifie le client {client.entreprise}.",
            )
            return redirect("clients")
    else:
        form = ClientForm(instance=client, user=request.user)

    return render(
        request,
        "clients/modifier_client.html",
        {"form": form, "client": client},
    )


def supprimer_client(request, id):
    client_queryset = Client.objects.all()
    if get_user_role(request.user) == "commercial":
        client_queryset = client_queryset.filter(commercial=request.user)
    client = get_object_or_404(client_queryset, id=id)
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

    form = ClientForm(request.POST, user=request.user)
    if form.is_valid():
        client = form.save(commit=False)
        if get_user_role(request.user) == "commercial":
            client.commercial = request.user
        client.save()
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


liste_clients = role_required("commercial", "responsable_commercial")(liste_clients)
ajouter_client = role_required("commercial", "responsable_commercial")(ajouter_client)
modifier_client = role_required("commercial", "responsable_commercial")(modifier_client)
supprimer_client = role_required("commercial", "responsable_commercial")(supprimer_client)
prospect_infos = role_required("commercial", "responsable_commercial")(prospect_infos)
ajouter_client_modal = role_required("commercial", "responsable_commercial")(ajouter_client_modal)
portefeuille_clients = role_required("responsable_commercial")(portefeuille_clients)
