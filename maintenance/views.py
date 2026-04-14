from django.db import transaction
from django.db.models import Count, Sum
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from chauffeurs.models import Chauffeur
from camions.models import Camion
from utilisateurs.permissions import role_required

from .forms import (
    MaintenanceAchatForm,
    MaintenanceGarageForm,
    MaintenanceGarageLigneFormSet,
    TypeMaintenanceForm,
)
from .models import Maintenance, MaintenanceSousLigne


def _maintenance_queryset():
    return Maintenance.objects.select_related("camion").prefetch_related(
        "lignes__type_maintenance",
        "lignes__sous_lignes",
    )


def _attach_subline_values(formset, request=None):
    for ligne_form in formset.forms:
        if request and request.method == "POST":
            ids = request.POST.getlist(f"subline-{ligne_form.prefix}-ids")
            labels = request.POST.getlist(f"subline-{ligne_form.prefix}-labels")
            quantities = request.POST.getlist(f"subline-{ligne_form.prefix}-quantites")
            values = [
                {
                    "id": ids[index] if index < len(ids) else "",
                    "libelle": labels[index] if index < len(labels) else "",
                    "quantite": quantities[index] if index < len(quantities) else "1",
                    "prix_unitaire": "0",
                    "montant": "0",
                }
                for index in range(max(len(labels), len(quantities), len(ids)))
            ]
        else:
            values = (
                list(
                    ligne_form.instance.sous_lignes.values(
                        "id",
                        "libelle",
                        "quantite",
                        "prix_unitaire",
                        "montant",
                    )
                )
                if ligne_form.instance.pk
                else []
            )
        ligne_form.subline_values = values or [
            {"id": "", "libelle": "", "quantite": "1", "prix_unitaire": "0", "montant": "0"}
        ]

    formset.empty_form.subline_values = [
        {"id": "", "libelle": "", "quantite": "1", "prix_unitaire": "0", "montant": "0"}
    ]


def _save_subline_items(request, formset):
    for ligne_form in formset.forms:
        if not hasattr(ligne_form, "cleaned_data"):
            continue
        if not ligne_form.cleaned_data or ligne_form.cleaned_data.get("DELETE"):
            continue

        ligne = ligne_form.instance
        if not ligne.pk:
            continue

        posted_ids = request.POST.getlist(f"subline-{ligne_form.prefix}-ids")
        labels = request.POST.getlist(f"subline-{ligne_form.prefix}-labels")
        quantities = request.POST.getlist(f"subline-{ligne_form.prefix}-quantites")

        kept_ids = []
        existing_by_id = {str(item.id): item for item in ligne.sous_lignes.all()}

        for index in range(max(len(labels), len(quantities), len(posted_ids))):
            subline_id = posted_ids[index].strip() if index < len(posted_ids) and posted_ids[index] else ""
            label = labels[index].strip() if index < len(labels) and labels[index] else ""
            quantity_raw = quantities[index].strip() if index < len(quantities) and quantities[index] else "1"

            if not label:
                continue

            if subline_id and subline_id in existing_by_id:
                subline = existing_by_id[subline_id]
                subline.libelle = label
                subline.quantite = quantity_raw or "1"
                subline.save()
                kept_ids.append(subline.id)
            else:
                subline = MaintenanceSousLigne.objects.create(
                    maintenance_ligne=ligne,
                    libelle=label,
                    quantite=quantity_raw or "1",
                )
                kept_ids.append(subline.id)

        ligne.sous_lignes.exclude(id__in=kept_ids).delete()


def _attach_achat_piece_rows(maintenance):
    piece_rows = []
    for ligne in maintenance.lignes.select_related("type_maintenance").prefetch_related("sous_lignes"):
        pieces = list(ligne.sous_lignes.all())
        if pieces:
            piece_rows.append({"ligne": ligne, "pieces": pieces})
        else:
            piece_rows.append({"ligne": ligne, "pieces": []})
    return piece_rows


def _save_achat_piece_prices(request, maintenance):
    for ligne in maintenance.lignes.prefetch_related("sous_lignes"):
        if ligne.sous_lignes.exists():
            for piece in ligne.sous_lignes.all():
                value = (request.POST.get(f"piece-price-{piece.id}") or "0").strip().replace(",", ".")
                piece.prix_unitaire = value or "0"
                piece.save()
        else:
            value = (request.POST.get(f"ligne-price-{ligne.id}") or "0").strip().replace(",", ".")
            ligne.prix_unitaire = value or "0"
            ligne.save()


def _maintenance_tabs_context(active_tab):
    return {"active_tab": active_tab}


def _garage_camions_catalog():
    chauffeurs_by_camion = {
        chauffeur.camion_id: chauffeur.nom
        for chauffeur in Chauffeur.objects.select_related("camion").filter(camion__isnull=False)
    }
    return [
        {
            "id": camion.id,
            "numero_tracteur": camion.numero_tracteur,
            "numero_citerne": camion.numero_citerne,
            "capacite": camion.capacite,
            "chauffeur": chauffeurs_by_camion.get(camion.id, ""),
        }
        for camion in Camion.objects.order_by("numero_tracteur")
    ]


def garage_maintenances(request):
    maintenances = list(_maintenance_queryset())
    for maintenance in maintenances:
        maintenance.pricing_complete = maintenance.is_pricing_complete()
    depenses_camions = (
        Maintenance.objects.values("camion__numero_tracteur", "camion__numero_citerne")
        .annotate(total_depense=Sum("total_facture"), total_maintenances=Count("id"))
        .order_by("-total_depense", "-total_maintenances")[:5]
    )
    return render(
        request,
        "maintenance/garage.html",
        {
            "maintenances": maintenances,
            "depenses_camions": depenses_camions,
            **_maintenance_tabs_context("garage"),
        },
    )


def achat_maintenances(request):
    historique = request.GET.get("scope") == "historique"
    maintenances = _maintenance_queryset()
    if historique:
        maintenances = maintenances.exclude(statut="en_cours")
    else:
        maintenances = maintenances.filter(statut="en_cours")
    return render(
        request,
        "maintenance/achat.html",
        {
            "maintenances": maintenances,
            "historique": historique,
            **_maintenance_tabs_context("achat"),
        },
    )


def _render_garage_form(request, template_name, form, formset, **context):
    _attach_subline_values(formset, request=request)
    return render(
        request,
        template_name,
        {
            "form": form,
            "formset": formset,
            "type_form": TypeMaintenanceForm(),
            "camions_catalog": _garage_camions_catalog(),
            **_maintenance_tabs_context("garage"),
            **context,
        },
    )


def _render_achat_form(request, template_name, form, **context):
    return render(
        request,
        template_name,
        {
            "form": form,
            "piece_rows": _attach_achat_piece_rows(context["maintenance"]),
            **_maintenance_tabs_context("achat"),
            **context,
        },
    )


def ajouter_maintenance_garage(request):
    if request.method == "POST":
        form = MaintenanceGarageForm(request.POST)
        formset = MaintenanceGarageLigneFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                maintenance = form.save(commit=False)
                maintenance.statut = "en_cours"
                maintenance.save()
                formset.instance = maintenance
                formset.save()
                _save_subline_items(request, formset)
                maintenance.refresh_total_facture()
            return redirect("garage_maintenances")
    else:
        form = MaintenanceGarageForm(initial={"statut": "en_cours"})
        formset = MaintenanceGarageLigneFormSet()

    return _render_garage_form(
        request,
        "maintenance/ajouter_maintenance_garage.html",
        form,
        formset,
    )


def modifier_maintenance_garage(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    if request.method == "POST":
        form = MaintenanceGarageForm(request.POST, instance=maintenance)
        formset = MaintenanceGarageLigneFormSet(request.POST, instance=maintenance)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                maintenance = form.save()
                formset.instance = maintenance
                formset.save()
                _save_subline_items(request, formset)
                maintenance.refresh_total_facture()
            return redirect("garage_maintenances")
    else:
        form = MaintenanceGarageForm(instance=maintenance)
        formset = MaintenanceGarageLigneFormSet(instance=maintenance)

    return _render_garage_form(
        request,
        "maintenance/modifier_maintenance_garage.html",
        form,
        formset,
        maintenance=maintenance,
    )


def modifier_maintenance_achat(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    if request.method == "POST":
        form = MaintenanceAchatForm(request.POST, instance=maintenance)
        if form.is_valid():
            with transaction.atomic():
                maintenance = form.save(commit=False)
                _save_achat_piece_prices(request, maintenance)
                maintenance.refresh_total_facture()
                if maintenance.statut == "terminee" and not maintenance.is_pricing_complete():
                    messages.error(request, "Impossible de terminer sans avoir saisi tous les prix des pieces.")
                else:
                    maintenance.save()
                    return redirect("achat_maintenances")
    else:
        form = MaintenanceAchatForm(instance=maintenance)

    return _render_achat_form(
        request,
        "maintenance/modifier_maintenance_achat.html",
        form,
        maintenance=maintenance,
    )


def terminer_maintenance(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if not maintenance.is_pricing_complete():
        messages.error(request, "Cette fiche est en attente de saisie de prix.")
        return redirect("garage_maintenances")
    maintenance.statut = "terminee"
    if not maintenance.date_fin:
        maintenance.date_fin = timezone.now()
    maintenance.save()
    return redirect("garage_maintenances")


def imprimer_maintenance(request, id):
    maintenance = get_object_or_404(
        Maintenance.objects.select_related("camion").prefetch_related(
            "lignes__type_maintenance",
            "lignes__sous_lignes",
        ),
        id=id,
    )
    chauffeur = Chauffeur.objects.filter(camion=maintenance.camion).first()
    return render(
        request,
        "maintenance/imprimer_maintenance.html",
        {
            "maintenance": maintenance,
            "chauffeur": chauffeur,
        },
    )


def supprimer_maintenance(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    camion = maintenance.camion
    maintenance.delete()

    maintenance_active = camion.maintenances.filter(statut="en_cours").exists()
    if not maintenance_active and camion.etat == "au_garage":
        camion.etat = "disponible"
        camion.save(update_fields=["etat"])

    return redirect("garage_maintenances")


def ajouter_type_maintenance_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = TypeMaintenanceForm(request.POST)
    if form.is_valid():
        type_maintenance = form.save()
        return JsonResponse(
            {
                "success": True,
                "type_maintenance": {
                    "id": type_maintenance.id,
                    "label": type_maintenance.libelle,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


liste_maintenances = role_required("logistique", "directeur")(garage_maintenances)
garage_maintenances = role_required("logistique", "directeur")(garage_maintenances)
achat_maintenances = role_required("logistique", "directeur")(achat_maintenances)
ajouter_maintenance_garage = role_required("logistique", "directeur")(ajouter_maintenance_garage)
modifier_maintenance_garage = role_required("logistique", "directeur")(modifier_maintenance_garage)
modifier_maintenance_achat = role_required("logistique", "directeur")(modifier_maintenance_achat)
terminer_maintenance = role_required("logistique", "directeur")(terminer_maintenance)
imprimer_maintenance = role_required("logistique", "directeur")(imprimer_maintenance)
supprimer_maintenance = role_required("logistique", "directeur")(supprimer_maintenance)
ajouter_type_maintenance_modal = role_required("logistique", "directeur")(ajouter_type_maintenance_modal)
