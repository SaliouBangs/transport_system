from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import DecimalField, Q, Sum
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date
from django.utils import timezone
from decimal import Decimal
from commandes.models import Commande
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, is_admin_user, role_required
from utilisateurs.constants import ROLE_COMMERCIAL, ROLE_RESPONSABLE_COMMERCIAL

from .forms import BanqueForm, ClientDestinationFormSet, ClientForm, EncaissementClientForm
from .models import (
    Banque,
    Client,
    EncaissementClient,
    EncaissementClientAllocation,
    commande_est_engagement,
    commande_est_facturee,
    commande_est_risque_potentiel,
    dernier_encaissement_sur_commande,
    latest_operation_for_commande,
    montant_total_commande,
    total_encaisse_sur_commande,
)


def _can_impute_client_avance(user):
    return is_admin_user(user) or get_user_role(user) in {"comptable", "comptable_sogefi"}


def _parse_decimal_input(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty")
    normalized = "".join(raw.split())
    has_comma = "," in normalized
    has_dot = "." in normalized
    if has_comma and has_dot:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif has_comma:
        normalized = normalized.replace(",", ".")
    return Decimal(normalized)


def _build_encaissement_allocations(request, client, type_encaissement, total_montant, encaissement_instance=None):
    if type_encaissement != "multi_commandes":
        return [], []

    cibles = request.POST.getlist("allocation_cible")
    montants = request.POST.getlist("allocation_montant")
    rows = []
    allocations = []
    total_allocations = Decimal("0.00")
    existing_allocations_commandes = {}
    existing_solde_initial = Decimal("0.00")
    if encaissement_instance and getattr(encaissement_instance, "pk", None):
        existing_allocations_commandes = {
            item.commande_id: item.montant_affecte or Decimal("0.00")
            for item in encaissement_instance.allocations.all()
            if item.cible_type == "commande" and item.commande_id
        }
        existing_solde_initial = sum(
            (
                item.montant_affecte or Decimal("0.00")
                for item in encaissement_instance.allocations.all()
                if item.cible_type == "solde_initial"
            ),
            Decimal("0.00"),
        )

    seen_targets = set()
    allocated_solde_initial = Decimal("0.00")
    for index, (cible, montant_raw) in enumerate(zip(cibles, montants), start=1):
        cible = (cible or "").strip()
        montant_raw = (montant_raw or "").strip()
        if not cible and not montant_raw:
            continue
        rows.append({"cible": cible, "montant": montant_raw})
        if not cible or not montant_raw:
            raise ValidationError(f"Ligne {index}: choisissez une affectation et un montant.")
        try:
            montant_value = _parse_decimal_input(montant_raw)
        except Exception:
            raise ValidationError(f"Ligne {index}: le montant affecte est invalide.")
        if montant_value <= 0:
            raise ValidationError(f"Ligne {index}: le montant affecte doit etre strictement positif.")

        if cible == "solde_initial":
            if cible in seen_targets:
                raise ValidationError("Le solde initial ne peut apparaitre qu'une seule fois dans la repartition.")
            solde_initial_restant = (client.solde_initial_restant or Decimal("0.00")) + existing_solde_initial
            if montant_value > solde_initial_restant:
                raise ValidationError(
                    f"Ligne {index}: le montant affecte depasse le solde initial restant ({solde_initial_restant})."
                )
            allocations.append({"cible_type": "solde_initial", "commande": None, "montant_affecte": montant_value})
            allocated_solde_initial += montant_value
        elif cible == "avance_client":
            if cible in seen_targets:
                raise ValidationError("L'avance client ne peut apparaitre qu'une seule fois dans la repartition.")
            allocations.append({"cible_type": "avance_client", "commande": None, "montant_affecte": montant_value})
        elif cible.startswith("commande:"):
            commande_id = cible.split(":", 1)[1].strip()
            commande = Commande.objects.filter(id=commande_id, client=client).first()
            if not commande:
                raise ValidationError(f"Ligne {index}: la commande selectionnee est introuvable pour ce client.")
            if not commande_est_facturee(commande):
                raise ValidationError(f"Ligne {index}: seule une commande livree peut etre reglee.")
            if commande.id in seen_targets:
                raise ValidationError("Une meme commande ne peut apparaitre qu'une seule fois dans la repartition.")
            montant_commande = montant_total_commande(commande)
            solde_commande = max(Decimal("0.00"), montant_commande - total_encaisse_sur_commande(commande))
            solde_commande += existing_allocations_commandes.get(commande.id, Decimal("0.00"))
            if montant_value > solde_commande:
                raise ValidationError(
                    f"Ligne {index}: le montant affecte depasse le solde restant de la commande ({solde_commande})."
                )
            allocations.append({"cible_type": "commande", "commande": commande, "montant_affecte": montant_value})
        else:
            raise ValidationError(f"Ligne {index}: l'affectation selectionnee est invalide.")

        seen_targets.add(cible)
        total_allocations += montant_value

    if not allocations:
        raise ValidationError("Ajoutez au moins une ligne d'affectation pour un paiement reparti.")
    if total_allocations != total_montant:
        raise ValidationError(
            f"Le total reparti ({total_allocations}) doit etre exactement egal au montant de l'encaissement ({total_montant})."
        )
    return allocations, rows


def _apply_date_range(queryset, field_name, date_debut=None, date_fin=None):
    if date_debut:
        queryset = queryset.filter(**{f"{field_name}__gte": date_debut})
    if date_fin:
        queryset = queryset.filter(**{f"{field_name}__lte": date_fin})
    return queryset


def _filtered_total_encaisse_sur_commande(commande, date_debut=None, date_fin=None):
    if not commande:
        return Decimal("0.00")

    direct_queryset = _apply_date_range(commande.encaissements_clients.all(), "date_encaissement", date_debut, date_fin)
    allocation_queryset = _apply_date_range(
        commande.encaissement_allocations.all(),
        "encaissement__date_encaissement",
        date_debut,
        date_fin,
    )
    total_direct = (
        direct_queryset.aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )
    total_reparti = (
        allocation_queryset.aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )
    return total_direct + total_reparti


def _filtered_dernier_encaissement_sur_commande(commande, date_debut=None, date_fin=None):
    if not commande:
        return None

    queryset = EncaissementClient.objects.filter(Q(commande=commande) | Q(allocations__commande=commande)).distinct()
    queryset = _apply_date_range(queryset, "date_encaissement", date_debut, date_fin)
    return queryset.order_by("-date_encaissement", "-id").first()


def _build_client_snapshot(client, date_debut=None, date_fin=None):
    commandes_queryset = _apply_date_range(
        Commande.objects.filter(client=client),
        "date_commande",
        date_debut,
        date_fin,
    ).prefetch_related("operations").order_by("-date_creation")

    encaissements_queryset = _apply_date_range(
        EncaissementClient.objects.filter(client=client),
        "date_encaissement",
        date_debut,
        date_fin,
    ).select_related("commande").prefetch_related("allocations__commande").order_by("-date_encaissement", "-id")

    commandes = []
    total_facture = Decimal("0.00")
    total_paye_commandes = Decimal("0.00")
    engagements = Decimal("0.00")
    risque_potentiel = Decimal("0.00")
    total_paye_solde_initial = Decimal("0.00")
    total_avances_recues = Decimal("0.00")
    total_avances_affectees = Decimal("0.00")

    commandes_liste = list(commandes_queryset)
    for commande in commandes_liste:
        montant_commande = montant_total_commande(commande)
        total_paye = _filtered_total_encaisse_sur_commande(commande, date_debut, date_fin)
        solde = max(Decimal("0.00"), montant_commande - total_paye)
        dernier_reglement = _filtered_dernier_encaissement_sur_commande(commande, date_debut, date_fin)
        latest_operation = latest_operation_for_commande(commande)
        est_facturee = commande_est_facturee(commande)
        est_engagement = commande_est_engagement(commande)
        est_risque_potentiel = commande_est_risque_potentiel(commande)
        total_paye_commandes += min(montant_commande, total_paye)

        if est_facturee:
            total_facture += montant_commande
        if est_engagement:
            engagements += montant_commande
        if est_risque_potentiel:
            risque_potentiel += montant_commande

        if est_facturee and latest_operation and latest_operation.date_bon_retour and solde <= Decimal("0.00"):
            etat_affiche = "Livree / retournee / soldee"
        elif est_facturee and latest_operation and latest_operation.date_bon_retour:
            etat_affiche = "Livree / retournee"
        elif est_facturee and latest_operation and (latest_operation.numero_facture or latest_operation.date_facture) and solde <= Decimal("0.00"):
            etat_affiche = "Livree / facturee / soldee"
        elif est_facturee and latest_operation and (latest_operation.numero_facture or latest_operation.date_facture):
            etat_affiche = "Livree / facturee"
        elif est_facturee:
            etat_affiche = "Livree"
        elif latest_operation and latest_operation.etat_bon == "charge":
            etat_affiche = "Chargee"
        else:
            etat_affiche = commande.get_statut_display()

        commandes.append(
            {
                "id": commande.id,
                "reference": commande.reference_affichee,
                "label": f"{commande.reference_affichee} - solde {solde}",
                "montant": str(montant_commande),
                "total_paye": str(total_paye),
                "solde": str(solde),
                "statut": commande.get_statut_display(),
                "etat_affiche": etat_affiche,
                "soldee": solde <= Decimal("0.00") and total_paye > Decimal("0.00"),
                "livree": est_facturee,
                "paiement_type": dernier_reglement.get_type_encaissement_display() if dernier_reglement else "",
                "paiement_mode": dernier_reglement.get_mode_paiement_display() if dernier_reglement else "",
                "paiement_reference": (dernier_reglement.reference or "") if dernier_reglement else "",
            }
        )

    encaissements = [
        {
            "date": encaissement.date_encaissement.strftime("%Y-%m-%d") if encaissement.date_encaissement else "",
            "montant": str(encaissement.montant or Decimal("0.00")),
            "type": encaissement.get_type_encaissement_display(),
            "reference": encaissement.reference or "-",
            "mode": encaissement.get_mode_paiement_display(),
            "commandes": encaissement.commandes_resume,
        }
        for encaissement in encaissements_queryset[:6]
    ]

    avances_disponibles = []
    for encaissement in encaissements_queryset.filter(type_encaissement="avance_client"):
        montant_affecte = (
            encaissement.allocations.filter(cible_type="commande").aggregate(
                total=Coalesce(
                    Sum("montant_affecte"),
                    Decimal("0.00"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).get("total")
            or Decimal("0.00")
        )
        disponible = max(Decimal("0.00"), (encaissement.montant or Decimal("0.00")) - montant_affecte)
        total_avances_recues += encaissement.montant or Decimal("0.00")
        total_avances_affectees += montant_affecte
        if disponible <= Decimal("0.00"):
            continue
        avances_disponibles.append(
            {
                "id": encaissement.id,
                "date": encaissement.date_encaissement.strftime("%Y-%m-%d") if encaissement.date_encaissement else "",
                "montant_disponible": str(disponible),
                "reference": encaissement.reference or "",
                "mode": encaissement.get_mode_paiement_display(),
            }
        )

    multi_solde_initial = (
        EncaissementClientAllocation.objects.filter(
            encaissement__in=encaissements_queryset,
            cible_type="solde_initial",
        ).aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )
    multi_avances = (
        EncaissementClientAllocation.objects.filter(
            encaissement__in=encaissements_queryset,
            cible_type="avance_client",
        ).aggregate(
            total=Coalesce(
                Sum("montant_affecte"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )

    if multi_avances > Decimal("0.00"):
        total_avances_recues += multi_avances

    total_paye_solde_initial = (
        encaissements_queryset.filter(type_encaissement="solde_initial").aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    ) + multi_solde_initial
    paiements_anticipes = max(Decimal("0.00"), total_avances_recues - total_avances_affectees)
    encours_client = max(Decimal("0.00"), total_facture - total_paye_commandes)
    reste_a_encaisser = encours_client
    exposition_client_totale = max(
        Decimal("0.00"),
        (client.solde_initial or Decimal("0.00"))
        - total_paye_solde_initial
        + encours_client
        + engagements
        + risque_potentiel
        - paiements_anticipes,
    )
    total_paye_global = (
        encaissements_queryset.aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )

    suggestions_avance = [
        {
            "commande_id": item["id"],
            "commande_reference": item["reference"],
            "solde": item["solde"],
        }
        for item in commandes
        if item["livree"] and not item["soldee"] and Decimal(item["solde"]) > Decimal("0.00")
    ]

    return {
        "commandes": commandes[:30],
        "encaissements": encaissements,
        "avances_disponibles": avances_disponibles,
        "suggestions_avance": suggestions_avance,
        "client_resume": {
            "solde_initial_restant": str(max(Decimal("0.00"), (client.solde_initial or Decimal("0.00")) - total_paye_solde_initial)),
            "encours_client": str(encours_client),
            "paiements_anticipes": str(paiements_anticipes),
            "engagements": str(engagements),
            "risque_potentiel": str(risque_potentiel),
            "reste_a_encaisser_reel": str(reste_a_encaisser),
            "total_facture": str(total_facture),
            "total_paye_commandes": str(total_paye_commandes),
            "total_paye_solde_initial": str(total_paye_solde_initial),
            "total_avances_recues": str(total_avances_recues),
            "total_avances_affectees": str(total_avances_affectees),
            "total_paye_global": str(total_paye_global),
            "exposition_client_totale": str(exposition_client_totale),
        },
    }


def liste_clients(request):
    clients = Client.objects.select_related("prospect", "commercial").prefetch_related("destinations", "encaissements")
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
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
    ).exclude(is_superuser=True).distinct().order_by("first_name", "last_name", "username")

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
        destination_formset = ClientDestinationFormSet(request.POST, prefix="destinations")
        if form.is_valid() and destination_formset.is_valid():
            client = form.save(commit=False)
            if get_user_role(request.user) == "commercial" and not is_admin_user(request.user):
                client.commercial = request.user
            client.save()
            destination_formset.instance = client
            destination_formset.save()
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
        destination_formset = ClientDestinationFormSet(prefix="destinations")

    return render(
        request,
        "clients/ajouter_client.html",
        {"form": form, "destination_formset": destination_formset},
    )


def modifier_client(request, id):
    client_queryset = Client.objects.all()
    if get_user_role(request.user) == "commercial" and not is_admin_user(request.user):
        client_queryset = client_queryset.filter(commercial=request.user)
    client = get_object_or_404(client_queryset, id=id)
    if request.method == "POST":
        form = ClientForm(request.POST, instance=client, user=request.user)
        destination_formset = ClientDestinationFormSet(request.POST, instance=client, prefix="destinations")
        if form.is_valid() and destination_formset.is_valid():
            client = form.save(commit=False)
            if get_user_role(request.user) == "commercial" and not is_admin_user(request.user):
                client.commercial = request.user
            client.save()
            destination_formset.save()
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
        destination_formset = ClientDestinationFormSet(instance=client, prefix="destinations")

    return render(
        request,
        "clients/modifier_client.html",
        {"form": form, "client": client, "destination_formset": destination_formset},
    )


def supprimer_client(request, id):
    client_queryset = Client.objects.all()
    if get_user_role(request.user) == "commercial" and not is_admin_user(request.user):
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
                "fonction": prospect.fonction,
                "telephone": prospect.telephone,
                "entreprise": prospect.entreprise,
                "ville": prospect.ville,
                "commercial_id": prospect.commercial_id,
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
        if get_user_role(request.user) == "commercial" and not is_admin_user(request.user):
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


def _build_encaissements_history_context(request):
    query = request.GET.get("q", "").strip()
    client_query = request.GET.get("client", "").strip()
    banque = request.GET.get("banque", "").strip()
    mode_paiement = request.GET.get("mode_paiement", "").strip()
    reference = request.GET.get("reference", "").strip()
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()
    commande_query = request.GET.get("commande", "").strip()

    encaissements = EncaissementClient.objects.select_related("client", "commande").prefetch_related("allocations__commande").order_by("-date_encaissement", "-id")
    if query:
        encaissements = encaissements.filter(
            Q(client__entreprise__icontains=query)
            | Q(client__nom__icontains=query)
            | Q(banque__icontains=query)
            | Q(reference__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(commande__operations__numero_bl__icontains=query)
            | Q(allocations__commande__reference__icontains=query)
            | Q(allocations__commande__operations__numero_bl__icontains=query)
        )
    if mode_paiement:
        encaissements = encaissements.filter(mode_paiement=mode_paiement)
    if date_debut:
        encaissements = encaissements.filter(date_encaissement__gte=date_debut)
    if date_fin:
        encaissements = encaissements.filter(date_encaissement__lte=date_fin)
    encaissements = encaissements.distinct()

    return {
        "encaissements": encaissements,
        "query": query,
        "client_query": client_query,
        "commande_query": commande_query,
        "banque": banque,
        "mode_paiement": mode_paiement,
        "reference": reference,
        "date_debut": date_debut,
        "date_fin": date_fin,
    }


def encaissements_clients(request):
    history_context = _build_encaissements_history_context(request)
    clients = Client.objects.order_by("entreprise", "nom")
    banques = Banque.objects.filter(actif=True).order_by("nom")
    allocation_rows = [{}]

    if request.method == "POST":
        form = EncaissementClientForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    encaissement = form.save(commit=False)
                    allocations, allocation_rows = _build_encaissement_allocations(
                        request,
                        encaissement.client,
                        encaissement.type_encaissement,
                        encaissement.montant or Decimal("0.00"),
                        encaissement_instance=encaissement,
                    )
                    encaissement.save()
                    if allocations:
                        EncaissementClientAllocation.objects.bulk_create(
                            [
                                EncaissementClientAllocation(
                                    encaissement=encaissement,
                                    cible_type=item["cible_type"],
                                    commande=item["commande"],
                                    montant_affecte=item["montant_affecte"],
                                )
                                for item in allocations
                            ]
                        )
                journaliser_action(
                    request.user,
                    "Clients",
                    "Encaissement client",
                    encaissement.client.entreprise,
                    f"{request.user.username} a enregistre un encaissement de {encaissement.montant} pour {encaissement.client.entreprise}.",
                )
                messages.success(request, "Encaissement enregistre.")
                return redirect("encaissements_clients")
            except ValidationError as exc:
                form.add_error(None, exc.message if hasattr(exc, "message") else str(exc))
    else:
        form = EncaissementClientForm()

    return render(
        request,
        "clients/encaissements.html",
        {
            "form": form,
            "clients": clients,
            "banques": banques,
            "mode_paiement_choices": EncaissementClient.MODE_PAIEMENT_CHOICES,
            "can_manage_encaissements": is_admin_user(request.user),
            "can_impute_avances": _can_impute_client_avance(request.user),
            "allocation_rows": allocation_rows,
            "banque_form": BanqueForm(),
            **history_context,
        },
    )


def historique_encaissements_clients(request):
    history_context = _build_encaissements_history_context(request)
    return render(
        request,
        "clients/encaissements_historique.html",
        {
            "mode_paiement_choices": EncaissementClient.MODE_PAIEMENT_CHOICES,
            "can_manage_encaissements": is_admin_user(request.user),
            "can_impute_avances": _can_impute_client_avance(request.user),
            **history_context,
        },
    )


def modifier_encaissement_client(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut modifier un encaissement.")
        return redirect("encaissements_clients")

    encaissement = get_object_or_404(EncaissementClient.objects.prefetch_related("allocations__commande"), id=id)
    allocation_rows = [
        {
            "cible": f"commande:{item.commande_id}" if item.cible_type == "commande" and item.commande_id else item.cible_type,
            "montant": str(item.montant_affecte),
        }
        for item in encaissement.allocations.all()
    ] or [{}]
    if request.method == "POST":
        form = EncaissementClientForm(request.POST, instance=encaissement)
        if form.is_valid():
            try:
                with transaction.atomic():
                    encaissement = form.save(commit=False)
                    allocations, allocation_rows = _build_encaissement_allocations(
                        request,
                        encaissement.client,
                        encaissement.type_encaissement,
                        encaissement.montant or Decimal("0.00"),
                        encaissement_instance=encaissement,
                    )
                    encaissement.save()
                    encaissement.allocations.all().delete()
                    if allocations:
                        EncaissementClientAllocation.objects.bulk_create(
                            [
                                EncaissementClientAllocation(
                                    encaissement=encaissement,
                                    cible_type=item["cible_type"],
                                    commande=item["commande"],
                                    montant_affecte=item["montant_affecte"],
                                )
                                for item in allocations
                            ]
                        )
                journaliser_action(
                    request.user,
                    "Clients",
                    "Modification d'encaissement",
                    encaissement.client.entreprise,
                    f"{request.user.username} a modifie un encaissement de {encaissement.montant} pour {encaissement.client.entreprise}.",
                )
                messages.success(request, "Encaissement mis a jour.")
                return redirect("encaissements_clients")
            except ValidationError as exc:
                form.add_error(None, exc.message if hasattr(exc, "message") else str(exc))
    else:
        form = EncaissementClientForm(instance=encaissement)

    clients = Client.objects.order_by("entreprise", "nom")
    banques = Banque.objects.filter(actif=True).order_by("nom")
    encaissements = EncaissementClient.objects.select_related("client", "commande").prefetch_related("allocations__commande").order_by("-date_encaissement", "-id")
    return render(
        request,
        "clients/encaissements.html",
        {
            "form": form,
            "encaissements": encaissements,
            "clients": clients,
            "banques": banques,
            "query": "",
            "client_query": "",
            "commande_query": "",
            "banque": "",
            "mode_paiement": "",
            "reference": "",
            "date_debut": "",
            "date_fin": "",
            "mode_paiement_choices": EncaissementClient.MODE_PAIEMENT_CHOICES,
            "can_manage_encaissements": True,
            "can_impute_avances": _can_impute_client_avance(request.user),
            "editing_encaissement": encaissement,
            "allocation_rows": allocation_rows,
            "banque_form": BanqueForm(),
        },
    )


def supprimer_encaissement_client(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut supprimer un encaissement.")
        return redirect("encaissements_clients")

    encaissement = get_object_or_404(EncaissementClient, id=id)
    if request.method == "POST":
        client_label = encaissement.client.entreprise
        montant = encaissement.montant
        encaissement.delete()
        journaliser_action(
            request.user,
            "Clients",
            "Suppression d'encaissement",
            client_label,
            f"{request.user.username} a supprime un encaissement de {montant} pour {client_label}.",
        )
        messages.success(request, "Encaissement supprime.")
    return redirect("encaissements_clients")


def commandes_client_infos(request):
    client_id = request.GET.get("client_id")
    date_debut = parse_date((request.GET.get("date_debut") or "").strip() or "")
    date_fin = parse_date((request.GET.get("date_fin") or "").strip() or "")
    if not client_id:
        return JsonResponse({"success": False, "errors": {"client": ["Client manquant."]}}, status=400)

    client = Client.objects.filter(id=client_id).first()
    if not client:
        return JsonResponse({"success": False, "errors": {"client": ["Client introuvable."]}}, status=404)
    snapshot = _build_client_snapshot(client, date_debut=date_debut, date_fin=date_fin)
    return JsonResponse({"success": True, **snapshot})


def ajouter_banque_modal(request):
    if request.method != "POST" or request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return JsonResponse({"success": False, "errors": {"__all__": ["Requete invalide."]}}, status=400)

    form = BanqueForm(request.POST)
    if form.is_valid():
        banque = form.save()
        return JsonResponse(
            {
                "success": True,
                "banque": {
                    "id": banque.id,
                    "label": banque.nom,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def imputer_avance_client(request, id):
    if not _can_impute_client_avance(request.user):
        messages.error(request, "Seuls l'administrateur et la comptabilite peuvent imputer une avance client.")
        return redirect("encaissements_clients")

    encaissement = get_object_or_404(
        EncaissementClient.objects.select_related("client").prefetch_related("allocations__commande"),
        id=id,
        type_encaissement="avance_client",
    )
    if request.method != "POST":
        return redirect("encaissements_clients")

    commande_id = (request.POST.get("commande_id") or "").strip()
    montant_raw = (request.POST.get("montant_affecte") or "").strip()

    if not commande_id:
        messages.error(request, "Choisissez une commande pour imputer l'avance.")
        return redirect("encaissements_clients")
    if not montant_raw:
        messages.error(request, "Saisissez le montant a imputer sur la commande.")
        return redirect("encaissements_clients")

    try:
        montant_a_imputer = _parse_decimal_input(montant_raw)
    except Exception:
        messages.error(request, "Le montant a imputer est invalide.")
        return redirect("encaissements_clients")

    if montant_a_imputer <= Decimal("0.00"):
        messages.error(request, "Le montant a imputer doit etre strictement positif.")
        return redirect("encaissements_clients")

    commande = Commande.objects.filter(id=commande_id, client=encaissement.client).first()
    if not commande:
        messages.error(request, "La commande choisie est introuvable pour ce client.")
        return redirect("encaissements_clients")

    avance_disponible = encaissement.montant_non_affecte
    if montant_a_imputer > avance_disponible:
        messages.error(
            request,
            f"Le montant saisi depasse l'avance disponible ({avance_disponible}).",
        )
        return redirect("encaissements_clients")

    montant_commande = (commande.quantite or Decimal("0.00")) * (commande.prix_negocie or Decimal("0.00"))
    solde_commande = montant_commande - total_encaisse_sur_commande(commande)
    if solde_commande <= Decimal("0.00"):
        messages.error(request, "Cette commande est deja entierement couverte.")
        return redirect("encaissements_clients")
    if montant_a_imputer > solde_commande:
        messages.error(
            request,
            f"Le montant saisi depasse le solde restant de la commande ({solde_commande}).",
        )
        return redirect("encaissements_clients")

    with transaction.atomic():
        allocation, created = EncaissementClientAllocation.objects.get_or_create(
            encaissement=encaissement,
            commande=commande,
            defaults={"montant_affecte": montant_a_imputer},
        )
        if not created:
            allocation.montant_affecte = (allocation.montant_affecte or Decimal("0.00")) + montant_a_imputer
            allocation.full_clean()
            allocation.save(update_fields=["montant_affecte"])

    journaliser_action(
        request.user,
        "Clients",
        "Imputation d'avance client",
        encaissement.client.entreprise,
        (
            f"{request.user.username} a impute {montant_a_imputer} de l'avance client "
            f"sur la commande {commande.reference_affichee} pour {encaissement.client.entreprise}."
        ),
    )
    messages.success(
        request,
        f"Avance imputee sur {commande.reference_affichee} pour {montant_a_imputer}.",
    )
    return redirect("encaissements_clients")


liste_clients = role_required(
    "commercial",
    "responsable_commercial",
    "directeur",
    "comptable",
    "comptable_sogefi",
    "caissiere",
    "dga",
    "dga_sogefi",
    "logistique",
    "transitaire",
    "maintenancier",
    "responsable_achat",
    "controleur",
)(liste_clients)
ajouter_client = role_required("commercial", "responsable_commercial")(ajouter_client)
modifier_client = role_required("commercial", "responsable_commercial")(modifier_client)
supprimer_client = role_required("directeur")(supprimer_client)
prospect_infos = role_required("commercial", "responsable_commercial")(prospect_infos)
ajouter_client_modal = role_required("commercial", "responsable_commercial")(ajouter_client_modal)
portefeuille_clients = role_required("responsable_commercial")(portefeuille_clients)
encaissements_clients = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(encaissements_clients)
historique_encaissements_clients = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(historique_encaissements_clients)
modifier_encaissement_client = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(modifier_encaissement_client)
supprimer_encaissement_client = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(supprimer_encaissement_client)
commandes_client_infos = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(commandes_client_infos)
ajouter_banque_modal = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(ajouter_banque_modal)
imputer_avance_client = role_required("commercial", "responsable_commercial", "directeur", "comptable", "comptable_sogefi")(imputer_avance_client)


def detail_client(request, id):
    client_queryset = Client.objects.select_related("prospect", "commercial").prefetch_related("destinations", "encaissements")
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
        client_queryset = client_queryset.filter(commercial=request.user)
    client = get_object_or_404(client_queryset, id=id)

    from commandes.models import Commande
    from operations.models import Operation

    statut_commande = request.GET.get("statut_commande", "").strip()
    etat_bl = request.GET.get("etat_bl", "").strip()
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()

    commandes = Commande.objects.filter(client=client).select_related("produit", "camion", "chauffeur").prefetch_related("operations").order_by("-date_creation")
    operations = Operation.objects.filter(client=client).select_related("commande", "produit", "camion", "chauffeur").order_by("-date_creation")
    encaissements = client.encaissements.select_related("commande").prefetch_related("allocations__commande").all()

    if date_debut:
        commandes = commandes.filter(date_commande__gte=date_debut)
        operations = operations.filter(date_creation__date__gte=date_debut)
        encaissements = encaissements.filter(date_encaissement__gte=date_debut)
    if date_fin:
        commandes = commandes.filter(date_commande__lte=date_fin)
        operations = operations.filter(date_creation__date__lte=date_fin)
        encaissements = encaissements.filter(date_encaissement__lte=date_fin)

    if statut_commande:
        commandes = commandes.filter(statut=statut_commande)
    if etat_bl:
        operations = operations.filter(etat_bon=etat_bl, remplace_par__isnull=True)
        commandes_filtrees = []
        for commande in commandes:
            latest_operation = (
                commande.operations.filter(remplace_par__isnull=True).order_by("-date_creation").first()
                or commande.operations.order_by("-date_creation").first()
            )
            if latest_operation and latest_operation.etat_bon == etat_bl:
                commandes_filtrees.append(commande.id)
        commandes = commandes.filter(id__in=commandes_filtrees)

    valorisation_commandes = Decimal("0.00")
    valorisation_livree = Decimal("0.00")
    total_essence = Decimal("0.00")
    total_gasoil = Decimal("0.00")
    for commande in commandes:
        commande.montant_commande_affiche = montant_total_commande(commande)
        valorisation_commandes += commande.montant_commande_affiche
        produit_nom = (commande.produit.nom or "").strip().upper() if commande.produit_id else ""
        if produit_nom == "ESSENCE":
            total_essence += commande.montant_commande_affiche
        elif produit_nom == "GASOIL":
            total_gasoil += commande.montant_commande_affiche

        montant_livre = Decimal("0.00")
        if commande_est_facturee(commande):
            montant_livre = commande.montant_commande_affiche
        commande.montant_livre_affiche = montant_livre
        valorisation_livree += montant_livre
        commande.total_encaisse_affiche = total_encaisse_sur_commande(commande)
        commande.solde_commande_affiche = max(Decimal("0.00"), commande.montant_commande_affiche - commande.total_encaisse_affiche)
        commande.dernier_reglement_affiche = dernier_encaissement_sur_commande(commande)

    # Calcul manuel pour garder une valorisation fiable sur quantite x prix_negocie.
    synthese_statuts = []
    for statut, _label in Commande.STATUT_CHOICES:
        total_statut = Decimal("0.00")
        for commande in Commande.objects.filter(client=client, statut=statut):
            total_statut += (commande.quantite or Decimal("0.00")) * (commande.prix_negocie or Decimal("0.00"))
        if total_statut > 0 or statut == statut_commande:
            synthese_statuts.append({"statut": statut, "total": total_statut})

    total_encaissements = encaissements.aggregate(
        total=Coalesce(Sum("montant"), Decimal("0.00"))
    ).get("total") or Decimal("0.00")
    total_encaissements_solde_initial = encaissements.filter(type_encaissement="solde_initial").aggregate(
        total=Coalesce(Sum("montant"), Decimal("0.00"))
    ).get("total") or Decimal("0.00")
    disponible_decouvert = (client.decouvert_maximum_autorise or Decimal("0.00")) - (client.risque_client or Decimal("0.00"))

    return render(
        request,
        "clients/detail_client.html",
        {
            "client": client,
            "commandes": commandes,
            "operations": operations,
            "encaissements": encaissements,
            "statut_commande": statut_commande,
            "etat_bl": etat_bl,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "statut_choices": Commande.STATUT_CHOICES,
            "etat_bl_choices": Operation.ETAT_BON_CHOICES,
            "valorisation_commandes": valorisation_commandes,
            "valorisation_livree": valorisation_livree,
            "total_encaissements": total_encaissements,
            "total_encaissements_solde_initial": total_encaissements_solde_initial,
            "solde_initial_restant": client.solde_initial_restant,
            "disponible_decouvert": disponible_decouvert,
            "paiements_anticipes": client.paiements_anticipes,
            "engagement_net": client.engagement_net,
            "risque_client": client.risque_client,
            "synthese_statuts": synthese_statuts,
            "total_essence": total_essence,
            "total_gasoil": total_gasoil,
        },
    )


def _build_line_chart(labels, series_specs):
    chart_width = 640
    chart_height = 220
    left_pad = 54
    right_pad = 18
    top_pad = 16
    bottom_pad = 26
    plot_width = chart_width - left_pad - right_pad
    plot_height = chart_height - top_pad - bottom_pad

    max_value = Decimal("0.00")
    for _name, _color, values in series_specs:
        for value in values:
            max_value = max(max_value, Decimal(value or Decimal("0.00")))
    if max_value <= Decimal("0.00"):
        max_value = Decimal("1.00")

    count = max(len(labels), 1)
    x_step = Decimal("0.00") if count <= 1 else Decimal(plot_width) / Decimal(count - 1)

    x_points = []
    for index, label in enumerate(labels):
        x = left_pad + float(x_step * index)
        x_points.append({"label": label, "x": round(x, 2)})

    series = []
    for name, color, values in series_specs:
        dots = []
        for index, raw_value in enumerate(values):
            value = Decimal(raw_value or Decimal("0.00"))
            ratio = float(value / max_value) if max_value > 0 else 0
            x = left_pad + float(x_step * index) if count > 1 else left_pad + (plot_width / 2)
            y = top_pad + (plot_height - (ratio * plot_height))
            dots.append(
                {
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "value": value,
                    "label": labels[index],
                }
            )
        series.append(
            {
                "name": name,
                "color": color,
                "points": " ".join(f"{dot['x']},{dot['y']}" for dot in dots),
                "dots": dots,
            }
        )

    y_ticks = []
    for tick_ratio in [1, Decimal("0.5"), Decimal("0.0")]:
        tick_value = max_value * Decimal(str(tick_ratio))
        y = top_pad + (plot_height - (float(tick_ratio) * plot_height))
        y_ticks.append({"label": tick_value, "y": round(y, 2)})

    return {
        "width": chart_width,
        "height": chart_height,
        "left_pad": left_pad,
        "right_pad": right_pad,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "x_points": x_points,
        "y_ticks": y_ticks,
        "series": series,
        "plot_bottom": top_pad + plot_height,
        "plot_right": left_pad + plot_width,
    }


def rapport_financier_client(request, id):
    client_queryset = Client.objects.select_related("prospect", "commercial").prefetch_related("destinations", "encaissements")
    if get_user_role(request.user) == ROLE_COMMERCIAL and not is_admin_user(request.user):
        client_queryset = client_queryset.filter(commercial=request.user)
    client = get_object_or_404(client_queryset, id=id)

    from commandes.models import Commande
    from operations.models import Operation

    date_debut = parse_date((request.GET.get("date_debut") or "").strip() or "")
    date_fin = parse_date((request.GET.get("date_fin") or "").strip() or "")

    commandes = _apply_date_range(
        Commande.objects.filter(client=client).select_related("produit").prefetch_related("operations").order_by("-date_creation"),
        "date_commande",
        date_debut,
        date_fin,
    )
    operations = _apply_date_range(
        Operation.objects.filter(client=client, remplace_par__isnull=True).select_related("commande", "produit").order_by("-date_creation"),
        "date_creation__date",
        date_debut,
        date_fin,
    )
    encaissements = _apply_date_range(
        client.encaissements.select_related("commande").prefetch_related("allocations__commande").all(),
        "date_encaissement",
        date_debut,
        date_fin,
    ).order_by("-date_encaissement", "-id")

    total_commandes = Decimal("0.00")
    total_livre = Decimal("0.00")
    total_encaisse_commandes = Decimal("0.00")
    total_essence = Decimal("0.00")
    total_gasoil = Decimal("0.00")
    total_solde = Decimal("0.00")
    top_impayes = []
    statut_totaux = []
    statut_map = {}

    for commande in commandes:
        montant = montant_total_commande(commande)
        total_commandes += montant
        total_paye = total_encaisse_sur_commande(commande)
        total_encaisse_commandes += min(montant, total_paye)
        solde = max(Decimal("0.00"), montant - total_paye)
        total_solde += solde
        produit_nom = (commande.produit.nom or "").strip().upper() if commande.produit_id else ""
        if produit_nom == "ESSENCE":
            total_essence += montant
        elif produit_nom == "GASOIL":
            total_gasoil += montant

        latest_operation = latest_operation_for_commande(commande)
        est_livree = commande_est_facturee(commande)
        if est_livree:
            total_livre += montant

        statut_label = latest_operation.get_etat_bon_display() if latest_operation else commande.get_statut_display()
        statut_map.setdefault(statut_label, Decimal("0.00"))
        statut_map[statut_label] += montant

        top_impayes.append(
            {
                "reference": commande.reference_affichee,
                "client": client.entreprise,
                "produit": commande.produit.nom if commande.produit_id else "-",
                "montant": montant,
                "paye": total_paye,
                "solde": solde,
                "statut": statut_label,
            }
        )

    paiements_anticipes = client.paiements_anticipes or Decimal("0.00")
    risque_net = client.risque_client or Decimal("0.00")
    dma = client.decouvert_maximum_autorise or Decimal("0.00")
    disponible = dma - risque_net
    ratio_dma = Decimal("0.00")
    if dma > 0:
        ratio_dma = (risque_net / dma) * Decimal("100.00")
    reste_a_encaisser = max(Decimal("0.00"), total_solde - paiements_anticipes)

    total_encaissements = (
        encaissements.aggregate(
            total=Coalesce(
                Sum("montant"),
                Decimal("0.00"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        ).get("total")
        or Decimal("0.00")
    )

    top_impayes = sorted(top_impayes, key=lambda item: item["solde"], reverse=True)[:6]
    statut_totaux = sorted(statut_map.items(), key=lambda item: item[1], reverse=True)
    statut_max = max((amount for _, amount in statut_totaux), default=Decimal("0.00"))
    statut_graph = []
    for label, amount in statut_totaux:
        percent = 0
        if statut_max > 0:
            percent = int((amount / statut_max) * 100)
        statut_graph.append({"label": label, "amount": amount, "percent": max(percent, 4 if amount > 0 else 0)})

    produit_total = total_essence + total_gasoil
    essence_percent = int((total_essence / produit_total) * 100) if produit_total > 0 else 0
    gasoil_percent = int((total_gasoil / produit_total) * 100) if produit_total > 0 else 0

    monthly_map = {}
    monthly_livres_map = {}
    monthly_essence_map = {}
    monthly_gasoil_map = {}
    for encaissement in encaissements:
        if not encaissement.date_encaissement:
            continue
        key = encaissement.date_encaissement.strftime("%Y-%m")
        monthly_map.setdefault(key, Decimal("0.00"))
        monthly_map[key] += encaissement.montant or Decimal("0.00")

    for commande in commandes:
        montant = (commande.quantite or Decimal("0.00")) * (commande.prix_negocie or Decimal("0.00"))
        produit_nom = (commande.produit.nom or "").strip().upper() if commande.produit_id else ""
        commande_key = commande.date_commande.strftime("%Y-%m") if commande.date_commande else ""
        if commande_key:
            if produit_nom == "ESSENCE":
                monthly_essence_map.setdefault(commande_key, Decimal("0.00"))
                monthly_essence_map[commande_key] += montant
            elif produit_nom == "GASOIL":
                monthly_gasoil_map.setdefault(commande_key, Decimal("0.00"))
                monthly_gasoil_map[commande_key] += montant
        latest_operation = (
            commande.operations.filter(remplace_par__isnull=True).order_by("-date_creation").first()
            or commande.operations.order_by("-date_creation").first()
        )
        if latest_operation and latest_operation.etat_bon == "livre" and latest_operation.date_bons_livres:
            key = latest_operation.date_bons_livres.strftime("%Y-%m")
            monthly_livres_map.setdefault(key, Decimal("0.00"))
            monthly_livres_map[key] += montant

    today = timezone.localdate()
    monthly_points = []
    year = today.year
    month = today.month
    month_keys = []
    for _ in range(8):
        key = f"{year:04d}-{month:02d}"
        month_keys.append(key)
        monthly_points.append((key, monthly_map.get(key, Decimal("0.00"))))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    month_keys.reverse()
    monthly_points.reverse()
    monthly_max = max((amount for _, amount in monthly_points), default=Decimal("0.00"))
    monthly_graph = []
    for key, amount in monthly_points:
        percent = 0
        if monthly_max > 0:
            percent = int((amount / monthly_max) * 100)
        monthly_graph.append(
            {
                "label": key,
                "amount": amount,
                "percent": max(percent, 8 if amount > 0 else 0),
            }
        )

    month_labels = [key[5:7] + "/" + key[0:4] for key in month_keys]
    encaissements_series = [monthly_map.get(key, Decimal("0.00")) for key in month_keys]
    livres_series = [monthly_livres_map.get(key, Decimal("0.00")) for key in month_keys]
    essence_series = [monthly_essence_map.get(key, Decimal("0.00")) for key in month_keys]
    gasoil_series = [monthly_gasoil_map.get(key, Decimal("0.00")) for key in month_keys]
    cashflow_chart = _build_line_chart(
        month_labels,
        [
            ("Encaissements", "#132f88", encaissements_series),
            ("Commandes livrees", "#7fc1ff", livres_series),
        ],
    )
    product_line_chart = _build_line_chart(
        month_labels,
        [
            ("Essence", "#0f766e", essence_series),
            ("Gasoil", "#d4a017", gasoil_series),
        ],
    )

    return render(
        request,
        "clients/rapport_financier_client.html",
        {
            "client": client,
            "date_debut": request.GET.get("date_debut", "").strip(),
            "date_fin": request.GET.get("date_fin", "").strip(),
            "total_commandes": total_commandes,
            "total_livre": total_livre,
            "total_encaisse": total_encaisse_commandes,
            "total_encaissements": total_encaissements,
            "total_essence": total_essence,
            "total_gasoil": total_gasoil,
            "paiements_anticipes": paiements_anticipes,
            "risque_net": risque_net,
            "reste_a_encaisser": reste_a_encaisser,
            "disponible": disponible,
            "dma": dma,
            "ratio_dma": ratio_dma,
            "top_impayes": top_impayes,
            "statut_graph": statut_graph,
            "monthly_graph": monthly_graph,
            "cashflow_chart": cashflow_chart,
            "product_line_chart": product_line_chart,
            "encaissements": encaissements[:8],
            "operations_count": operations.count(),
            "essence_percent": essence_percent,
            "gasoil_percent": gasoil_percent,
        },
    )


detail_client = role_required(
    "commercial",
    "responsable_commercial",
    "directeur",
    "comptable",
    "comptable_sogefi",
    "caissiere",
    "dga",
    "dga_sogefi",
    "logistique",
    "transitaire",
    "maintenancier",
    "responsable_achat",
    "controleur",
)(detail_client)
rapport_financier_client = role_required(
    "commercial",
    "responsable_commercial",
    "directeur",
    "comptable",
    "comptable_sogefi",
    "caissiere",
    "dga",
    "dga_sogefi",
    "logistique",
    "transitaire",
    "maintenancier",
    "responsable_achat",
    "controleur",
)(rapport_financier_client)
