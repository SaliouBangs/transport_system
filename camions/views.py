from decimal import Decimal

from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Avg, Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import is_admin_user, role_required

from maintenance.models import MaintenanceSousLigne
from .forms import (
    AffreteAdminCreationForm,
    AffreteCamionAdminForm,
    CamionForm,
    TransporteurForm,
)
from .models import Camion, Transporteur


def _compute_camion_costs(camion, maintenances=None, operations=None):
    maintenances = maintenances if maintenances is not None else list(camion.maintenances.all())
    operations = operations if operations is not None else list(camion.operations.all())
    maintenance_total = Decimal("0")
    maintenance_payable_total = Decimal("0")
    for maintenance in maintenances:
        maintenance_total += maintenance.total_global_information or Decimal("0")
        maintenance_payable_total += maintenance.total_facture or Decimal("0")

    depenses_total = Decimal("0")
    for operation in operations:
        for depense in operation.depenses_liees.all():
            depenses_total += depense.montant_total or Decimal("0")

    return {
        "maintenance_total": maintenance_total,
        "maintenance_payable_total": maintenance_payable_total,
        "depenses_total": depenses_total,
        "cout_total": maintenance_total + depenses_total,
    }


def _row_matches_search(value, search):
    return search in (value or "").lower()


def _get_camion_report_context(camion, request=None):
    query = ((request.GET.get("q") if request else "") or "").strip().lower()
    date_from = ((request.GET.get("date_from") if request else "") or "").strip()
    date_to = ((request.GET.get("date_to") if request else "") or "").strip()

    operations = list(camion.operations.all())
    maintenances = list(camion.maintenances.all())

    if date_from or date_to or query:
        filtered_operations = []
        for operation in operations:
            operation_date = (
                operation.date_bons_livres
                or operation.date_bons_charges
                or operation.date_liquidation
                or operation.date_declaration
                or operation.date_bl
            )
            if date_from and operation_date and operation_date.date().isoformat() < date_from:
                continue
            if date_to and operation_date and operation_date.date().isoformat() > date_to:
                continue
            if query:
                searchable = " ".join(
                    [
                        str(operation.numero_bl or ""),
                        str(getattr(operation.commande, "reference", "") or ""),
                        str(getattr(operation.client, "nom_client", "") or operation.client or ""),
                        str(operation.produit or ""),
                        str(getattr(operation.chauffeur, "nom", "") or ""),
                        str(operation.get_etat_bon_display() or ""),
                    ]
                ).lower()
                if query not in searchable:
                    continue
            filtered_operations.append(operation)
        operations = filtered_operations

        filtered_maintenances = []
        for maintenance in maintenances:
            maintenance_date = maintenance.date_fin or maintenance.date_debut
            if date_from and maintenance_date and maintenance_date.date().isoformat() < date_from:
                continue
            if date_to and maintenance_date and maintenance_date.date().isoformat() > date_to:
                continue
            if query:
                diagnostics = []
                for ligne in maintenance.lignes.all():
                    diagnostics.append(ligne.libelle or "")
                    diagnostics.extend(piece.libelle or "" for piece in ligne.sous_lignes.all())
                searchable = " ".join(
                    [
                        str(maintenance.reference or ""),
                        str(maintenance.get_statut_display() or ""),
                        str(maintenance.prestataire or ""),
                        str(getattr(maintenance.fournisseur, "nom_fournisseur", "") or ""),
                        *diagnostics,
                    ]
                ).lower()
                if query not in searchable:
                    continue
            filtered_maintenances.append(maintenance)
        maintenances = filtered_maintenances

    couts = _compute_camion_costs(camion, maintenances=maintenances, operations=operations)
    depenses = [
        depense
        for operation in operations
        for depense in operation.depenses_liees.all()
    ]

    quantite_transportee = sum((operation.quantite or 0) for operation in operations)
    commandes_transportees = {operation.commande_id for operation in operations if operation.commande_id}

    if query:
        depenses = [
            depense for depense in depenses
            if query in " ".join(
                [
                    str(depense.reference or ""),
                    str(depense.type_depense or ""),
                    str(depense.get_statut_display() or ""),
                ]
            ).lower()
        ]

    panne_qs = MaintenanceSousLigne.objects.filter(
        maintenance_ligne__maintenance__camion=camion,
        maintenance_ligne__maintenance__in=maintenances,
    )
    if query:
        panne_qs = panne_qs.filter(
            Q(libelle__icontains=query)
            | Q(panne_catalogue__type_maintenance__libelle__icontains=query)
        )
    panne_rows = (
        panne_qs
        .values(
            "panne_catalogue__type_maintenance__libelle",
            "libelle",
        )
        .annotate(
            total=Count("id"),
            prix_moyen=Avg("prix_unitaire"),
            montant_total=Sum("montant"),
        )
        .order_by("-montant_total", "-total", "panne_catalogue__type_maintenance__libelle", "libelle")
    )

    return {
        "camion": camion,
        "operations": operations,
        "maintenances": maintenances,
        "depenses": depenses,
        "panne_rows": panne_rows,
        "quantite_transportee": quantite_transportee,
        "commandes_transportees_count": len(commandes_transportees),
        "filter_values": {
            "q": query,
            "date_from": date_from,
            "date_to": date_to,
        },
        **couts,
    }


def _get_camion_report_queryset():
    return Camion.objects.select_related("transporteur").prefetch_related(
        "maintenances__lignes__sous_lignes__article_stock",
        "maintenances__lignes__sous_lignes__panne_catalogue__type_maintenance",
        "operations__client",
        "operations__chauffeur",
        "operations__commande",
        "operations__depenses_liees__lignes",
    )


def liste_camions(request):
    panne_threshold = 3
    camions = Camion.objects.select_related("transporteur").prefetch_related(
        "maintenances__lignes__sous_lignes",
        "operations__depenses_liees__lignes",
    ).annotate(
        panne_count=Count("maintenances", distinct=True)
    )
    for camion in camions:
        couts = _compute_camion_costs(camion)
        camion.cout_total = couts["cout_total"]
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
        {
            "camions": camions,
            "panne_threshold": panne_threshold,
            "can_manage_affretes_admin": is_admin_user(request.user),
        },
    )


def detail_camion(request, id):
    camion = get_object_or_404(_get_camion_report_queryset(), id=id)
    return render(request, "camions/detail_camion.html", _get_camion_report_context(camion, request=request))


def rapport_camions(request):
    selected_camion_id = (request.GET.get("camion") or "").strip()
    camions = Camion.objects.select_related("transporteur").order_by("numero_tracteur", "numero_citerne")
    context = {
        "camions_choices": camions,
        "selected_camion_id": selected_camion_id,
    }
    if not selected_camion_id:
        return render(request, "camions/rapport_camions.html", context)

    camion = get_object_or_404(_get_camion_report_queryset(), id=selected_camion_id)
    context.update(_get_camion_report_context(camion, request=request))
    context["report_reset_url"] = f"/camions/rapports/?camion={camion.id}"
    return render(request, "camions/rapport_camions.html", context)

def imprimer_camion(request, id):
    camion = get_object_or_404(_get_camion_report_queryset(), id=id)
    return render(request, "camions/imprimer_camion.html", _get_camion_report_context(camion))


def gestion_affretes(request):
    affretes = (
        Transporteur.objects.filter(camions__est_affrete=True)
        .prefetch_related("camions__chauffeur_set")
        .distinct()
        .order_by("nom")
    )
    total_camions_affretes = sum(affrete.camions.count() for affrete in affretes)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "nouveau_affrete":
            nouveau_form = AffreteAdminCreationForm(request.POST)
            camion_form = AffreteCamionAdminForm()
            if nouveau_form.is_valid():
                camion = nouveau_form.save()
                chauffeur = camion.chauffeur_set.order_by("nom").first()
                journaliser_action(
                    request.user,
                    "Camions",
                    "Creation affrete",
                    camion.transporteur.nom if camion.transporteur else camion.numero_tracteur,
                    (
                        f"{request.user.username} a cree l'affrete "
                        f"{camion.transporteur.nom if camion.transporteur else '-'} "
                        f"avec le camion {camion.numero_tracteur}"
                        f"{' et le chauffeur ' + chauffeur.nom if chauffeur else ''}."
                    ),
                )
                messages.success(request, "Nouvel affrete enregistre avec succes.")
                return redirect("gestion_affretes")
        elif action == "camion_affrete":
            nouveau_form = AffreteAdminCreationForm()
            camion_form = AffreteCamionAdminForm(request.POST)
            if camion_form.is_valid():
                camion = camion_form.save()
                chauffeur = camion.chauffeur_set.order_by("nom").first()
                journaliser_action(
                    request.user,
                    "Camions",
                    "Ajout camion affrete",
                    camion.transporteur.nom if camion.transporteur else camion.numero_tracteur,
                    (
                        f"{request.user.username} a ajoute le camion {camion.numero_tracteur} "
                        f"a l'affrete {camion.transporteur.nom if camion.transporteur else '-'}"
                        f"{' avec le chauffeur ' + chauffeur.nom if chauffeur else ''}."
                    ),
                )
                messages.success(request, "Camion affrete enregistre avec succes.")
                return redirect("gestion_affretes")
        else:
            nouveau_form = AffreteAdminCreationForm()
            camion_form = AffreteCamionAdminForm()
            messages.error(request, "Action affrete inconnue.")
    else:
        nouveau_form = AffreteAdminCreationForm()
        camion_form = AffreteCamionAdminForm()

    return render(
        request,
        "camions/affretes.html",
        {
            "nouveau_affrete_form": nouveau_form,
            "camion_affrete_form": camion_form,
            "affretes": affretes,
            "total_camions_affretes": total_camions_affretes,
        },
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


liste_camions = role_required("logistique", "maintenancier", "dga", "directeur", "invite", "controleur")(liste_camions)
detail_camion = role_required("logistique", "maintenancier", "dga", "directeur", "invite", "controleur", "chef_chauffeur")(detail_camion)
rapport_camions = role_required("logistique", "maintenancier", "dga", "directeur", "invite", "controleur", "chef_chauffeur")(rapport_camions)
imprimer_camion = role_required("logistique", "maintenancier", "dga", "directeur", "invite", "controleur", "chef_chauffeur")(imprimer_camion)
gestion_affretes = role_required()(gestion_affretes)
ajouter_camion = role_required("logistique", "maintenancier", "dga", "directeur")(ajouter_camion)
modifier_camion = role_required("logistique", "maintenancier", "dga", "directeur")(modifier_camion)
supprimer_camion = role_required("logistique", "maintenancier", "dga", "directeur")(supprimer_camion)
ajouter_transporteur_modal = role_required("logistique", "maintenancier", "dga", "directeur")(ajouter_transporteur_modal)
