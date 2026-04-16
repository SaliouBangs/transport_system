from io import BytesIO

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from chauffeurs.models import Chauffeur
from camions.models import Camion
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, is_admin_user, role_required

from .forms import (
    FournisseurForm,
    MaintenanceAchatForm,
    MaintenanceGarageForm,
    MaintenanceGarageLigneFormSet,
    PrestataireForm,
    TypeMaintenanceForm,
)
from .models import Fournisseur, Maintenance, MaintenanceSousLigne, Prestataire


def _maintenance_queryset():
    return Maintenance.objects.select_related("camion", "fournisseur").prefetch_related(
        "lignes__type_maintenance",
        "lignes__sous_lignes",
    )


def _apply_maintenance_filters(request, queryset):
    q = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    statut = (request.GET.get("statut") or "").strip()

    if q:
        queryset = queryset.filter(
            Q(reference__icontains=q)
            | Q(camion__code_camion__icontains=q)
            | Q(camion__numero_tracteur__icontains=q)
            | Q(camion__numero_citerne__icontains=q)
            | Q(camion__chauffeur__nom__icontains=q)
        ).distinct()

    if date_from:
        queryset = queryset.filter(date_debut__date__gte=date_from)

    if date_to:
        queryset = queryset.filter(date_debut__date__lte=date_to)

    if statut:
        queryset = queryset.filter(statut=statut)

    return queryset, {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "statut": statut,
    }


def _maintenance_export_rows(queryset):
    rows = []
    for maintenance in queryset:
        rows.append(
            [
                maintenance.reference,
                maintenance.camion.code_camion,
                maintenance.camion.numero_tracteur,
                maintenance.camion.numero_citerne or "",
                (
                    Chauffeur.objects.filter(camion=maintenance.camion)
                    .values_list("nom", flat=True)
                    .first()
                    or ""
                ),
                maintenance.get_statut_display(),
                maintenance.date_debut.strftime("%Y-%m-%d %H:%M"),
                maintenance.date_fin.strftime("%Y-%m-%d %H:%M") if maintenance.date_fin else "",
                str(maintenance.total_facture),
                maintenance.numero_facture or "",
                maintenance.date_paiement.strftime("%Y-%m-%d") if maintenance.date_paiement else "",
            ]
        )
    return rows


def _export_maintenance_xls(queryset, filename):
    try:
        from openpyxl import Workbook
    except ImportError:
        return HttpResponse(
            "Le module openpyxl n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Maintenance"
    sheet.append(
        [
            "Reference",
            "Code camion",
            "Tracteur",
            "Citerne",
            "Chauffeur",
            "Statut",
            "Date entree",
            "Date sortie",
            "Montant",
            "Numero facture",
            "Date paiement",
        ]
    )
    for row in _maintenance_export_rows(queryset):
        sheet.append(row)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    workbook.save(response)
    return response


def _export_maintenance_pdf(queryset, filename):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except ImportError:
        return HttpResponse(
            "Le module reportlab n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    data = [[
        "Reference",
        "Code camion",
        "Tracteur",
        "Citerne",
        "Chauffeur",
        "Statut",
        "Entree",
        "Sortie",
        "Montant",
        "Facture",
        "Paiement",
    ]]
    data.extend(_maintenance_export_rows(queryset))

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123047")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2e8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f8fb")]),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    doc.build([table])

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    response.write(buffer.getvalue())
    return response


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


def _save_subline_items(request, formset, allow_create=True, allow_delete=True):
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
            elif allow_create:
                subline = MaintenanceSousLigne.objects.create(
                    maintenance_ligne=ligne,
                    libelle=label,
                    quantite=quantity_raw or "1",
                )
                kept_ids.append(subline.id)

        if allow_delete:
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
            "code_camion": camion.code_camion,
            "numero_tracteur": camion.numero_tracteur,
            "numero_citerne": camion.numero_citerne,
            "capacite": camion.capacite,
            "chauffeur": chauffeurs_by_camion.get(camion.id, ""),
        }
        for camion in Camion.objects.order_by("numero_tracteur")
    ]


def garage_maintenances(request):
    maintenances_qs, filter_values = _apply_maintenance_filters(request, _maintenance_queryset())
    maintenances = list(maintenances_qs)
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    for maintenance in maintenances:
        maintenance.pricing_complete = maintenance.is_pricing_complete()
        maintenance.can_validate_logistique = (
            user_role == "logistique"
            and maintenance.statut == "en_cours"
            and maintenance.pricing_complete
            and not maintenance.is_validated_by_logistique()
        )
        maintenance.can_reject_dga = (
            user_role == "dga"
            and maintenance.statut == "en_cours"
            and maintenance.is_validated_by_logistique()
            and not maintenance.is_validated_by_dga()
        )
        maintenance.can_validate_dga = (
            user_role == "dga"
            and maintenance.statut == "en_cours"
            and maintenance.is_validated_by_logistique()
            and not maintenance.is_validated_by_dga()
        )
        maintenance.can_reject_dg = (
            user_role == "directeur"
            and maintenance.statut == "en_cours"
            and maintenance.is_validated_by_dga()
            and not maintenance.is_validated_by_dg()
        )
        maintenance.can_validate_dg = (
            user_role == "directeur"
            and maintenance.statut == "en_cours"
            and maintenance.is_validated_by_dga()
            and not maintenance.is_validated_by_dg()
        )
        if maintenance.statut == "refusee":
            maintenance.validation_status_label = "Rejete par le DGA"
            maintenance.validation_status_variant = "danger"
        elif maintenance.statut == "annulee":
            maintenance.validation_status_label = "Rejete par le DG"
            maintenance.validation_status_variant = "danger"
        elif maintenance.statut == "terminee":
            maintenance.validation_status_label = "Validation complete"
            maintenance.validation_status_variant = "ok"
        elif not maintenance.is_validated_by_logistique():
            maintenance.validation_status_label = "En attente validation logistique"
            maintenance.validation_status_variant = "warning"
        elif not maintenance.is_validated_by_dga():
            maintenance.validation_status_label = "En attente validation DGA"
            maintenance.validation_status_variant = "warning"
        elif not maintenance.is_validated_by_dg():
            maintenance.validation_status_label = "En attente validation DG"
            maintenance.validation_status_variant = "warning"
        else:
            maintenance.validation_status_label = "Validation complete"
            maintenance.validation_status_variant = "ok"
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
            "filter_values": filter_values,
            "statut_choices": Maintenance.STATUT_CHOICES,
            "is_admin_maintenance": is_admin,
            **_maintenance_tabs_context("garage"),
        },
    )


def achat_maintenances(request):
    historique = request.GET.get("scope") == "historique"
    user_role = get_user_role(request.user)
    can_edit_achat = is_admin_user(request.user) or user_role == "logistique"
    maintenances = _maintenance_queryset()
    if historique:
        maintenances = maintenances.exclude(statut="en_cours")
    else:
        maintenances = maintenances.filter(statut="en_cours")
    maintenances, filter_values = _apply_maintenance_filters(request, maintenances)
    return render(
        request,
        "maintenance/achat.html",
        {
            "maintenances": maintenances,
            "historique": historique,
            "filter_values": filter_values,
            "statut_choices": Maintenance.STATUT_CHOICES,
            "is_admin_maintenance": is_admin_user(request.user),
            "can_edit_achat": can_edit_achat,
            **_maintenance_tabs_context("achat"),
        },
    )


def fournisseurs_maintenance(request):
    query = (request.GET.get("q") or "").strip()
    fournisseurs = Fournisseur.objects.all().order_by("nom_fournisseur", "entreprise")
    if query:
        fournisseurs = fournisseurs.filter(
            Q(nom_fournisseur__icontains=query)
            | Q(entreprise__icontains=query)
            | Q(domaine_activite__icontains=query)
            | Q(numero_telephone__icontains=query)
        )
    form = FournisseurForm()
    return render(
        request,
        "maintenance/fournisseurs.html",
        {
            "fournisseurs": fournisseurs,
            "query": query,
            "form": form,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("achat"),
        },
    )


def ajouter_fournisseur(request):
    if request.method != "POST":
        return redirect("fournisseurs_maintenance")

    form = FournisseurForm(request.POST)
    if form.is_valid():
        fournisseur = form.save()
        log_action(
            request.user,
            "maintenance",
            "creation fournisseur",
            f"{request.user.username} a cree le fournisseur {fournisseur}.",
        )
        messages.success(request, f"Le fournisseur {fournisseur} a ete cree.")
        return redirect("fournisseurs_maintenance")

    fournisseurs = Fournisseur.objects.all().order_by("nom_fournisseur", "entreprise")
    messages.error(request, "Impossible de creer le fournisseur. Verifiez les champs puis reessayez.")
    return render(
        request,
        "maintenance/fournisseurs.html",
        {
            "fournisseurs": fournisseurs,
            "query": "",
            "form": form,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("achat"),
        },
        status=400,
    )


def modifier_fournisseur(request, id):
    fournisseur = get_object_or_404(Fournisseur, pk=id)
    if request.method == "POST":
        form = FournisseurForm(request.POST, instance=fournisseur)
        if form.is_valid():
            fournisseur = form.save()
            log_action(
                request.user,
                "maintenance",
                "mise a jour fournisseur",
                f"{request.user.username} a modifie le fournisseur {fournisseur}.",
            )
            messages.success(request, f"Le fournisseur {fournisseur} a ete mis a jour.")
            return redirect("fournisseurs_maintenance")
        messages.error(request, "Impossible de mettre a jour le fournisseur. Verifiez les champs.")
    else:
        form = FournisseurForm(instance=fournisseur)

    return render(
        request,
        "maintenance/modifier_fournisseur.html",
        {
            "form": form,
            "fournisseur": fournisseur,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("achat"),
        },
        status=400 if request.method == "POST" and form.errors else 200,
    )


@require_POST
def supprimer_fournisseur(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut supprimer un fournisseur.")
        return redirect("fournisseurs_maintenance")

    fournisseur = get_object_or_404(Fournisseur, pk=id)
    label = str(fournisseur)
    fournisseur.delete()
    log_action(
        request.user,
        "maintenance",
        "suppression fournisseur",
        f"{request.user.username} a supprime le fournisseur {label}.",
    )
    messages.success(request, f"Le fournisseur {label} a ete supprime.")
    return redirect("fournisseurs_maintenance")


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
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("garage"),
            **context,
        },
    )


def _render_achat_form(request, template_name, form, **context):
    fournisseurs_catalog = [
        {"id": fournisseur.id, "label": str(fournisseur)}
        for fournisseur in Fournisseur.objects.all()
    ]
    prestataires_catalog = [
        {"label": str(prestataire)}
        for prestataire in Prestataire.objects.all()
    ]
    if context["maintenance"].prestataire and not any(
        item["label"] == context["maintenance"].prestataire for item in prestataires_catalog
    ):
        prestataires_catalog.insert(0, {"label": context["maintenance"].prestataire})
    return render(
        request,
        template_name,
        {
            "form": form,
            "fournisseur_form": FournisseurForm(),
            "prestataire_form": PrestataireForm(),
            "piece_rows": _attach_achat_piece_rows(context["maintenance"]),
            "fournisseurs_catalog": fournisseurs_catalog,
            "prestataires_catalog": prestataires_catalog,
            "is_admin_maintenance": is_admin_user(request.user),
            "can_edit_achat": context.get("can_edit_achat", False),
            **_maintenance_tabs_context("achat"),
            **context,
        },
    )


def _set_form_read_only(form):
    for field in form.fields.values():
        field.disabled = True


def _set_formset_read_only(formset):
    for form in formset.forms:
        for field in form.fields.values():
            field.disabled = True


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
                maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Creation de diagnostic",
                    maintenance_label,
                    f"{request.user.username} a cree le diagnostic {maintenance_label} pour le camion {maintenance.camion}.",
                )
            return redirect("garage_maintenances")
    else:
        form = MaintenanceGarageForm(initial={"statut": "en_cours"})
        formset = MaintenanceGarageLigneFormSet()

    return _render_garage_form(
        request,
        "maintenance/ajouter_maintenance_garage.html",
        form,
        formset,
        submit_label="Enregistrer le diagnostic",
    )


def modifier_maintenance_garage(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    can_edit_diagnostic = is_admin or user_role == "maintenancier"
    if request.method == "POST":
        if not can_edit_diagnostic:
            messages.error(request, "Seuls le maintenancier et l'administrateur peuvent modifier le diagnostic.")
            return redirect("modifier_maintenance_garage", id=maintenance.id)
        form = MaintenanceGarageForm(request.POST, instance=maintenance)
        formset = MaintenanceGarageLigneFormSet(request.POST, instance=maintenance)
        if form.is_valid() and formset.is_valid():
            if not is_admin and any(
                ligne_form.cleaned_data.get("DELETE")
                for ligne_form in formset.forms
                if hasattr(ligne_form, "cleaned_data")
            ):
                messages.error(request, "Seul l'administrateur peut supprimer une ligne de panne.")
                return redirect("modifier_maintenance_garage", id=maintenance.id)
            with transaction.atomic():
                maintenance = form.save()
                formset.instance = maintenance
                formset.save()
                _save_subline_items(
                    request,
                    formset,
                    allow_create=can_edit_diagnostic,
                    allow_delete=is_admin,
                )
                maintenance.refresh_total_facture()
                maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Modification de diagnostic",
                    maintenance_label,
                    f"{request.user.username} a modifie le diagnostic {maintenance_label}.",
                )
            return redirect("garage_maintenances")
    else:
        form = MaintenanceGarageForm(instance=maintenance)
        formset = MaintenanceGarageLigneFormSet(instance=maintenance)
        if not can_edit_diagnostic:
            _set_form_read_only(form)
            _set_formset_read_only(formset)

    return _render_garage_form(
        request,
        "maintenance/modifier_maintenance_garage.html",
        form,
        formset,
        maintenance=maintenance,
        read_only=not can_edit_diagnostic,
        allow_structure_changes=can_edit_diagnostic,
        can_delete_lines=is_admin,
        submit_label="Mettre a jour le diagnostic",
    )


def modifier_maintenance_achat(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    can_edit_achat = is_admin or (
        user_role == "logistique"
        and maintenance.statut == "en_cours"
        and not maintenance.is_validated_by_dga()
        and not maintenance.is_validated_by_dg()
    )
    if request.method == "POST":
        if not can_edit_achat:
            messages.error(request, "Seuls la logistique et l'administrateur peuvent modifier les achats avant decision finale.")
            return redirect("achat_maintenances")
        post_data = request.POST.copy()
        if not is_admin:
            post_data["statut"] = maintenance.statut
        post_data["prestataire"] = (post_data.get("prestataire_search") or post_data.get("prestataire") or "").strip()
        form = MaintenanceAchatForm(post_data, instance=maintenance)
        if form.is_valid():
            with transaction.atomic():
                maintenance = form.save(commit=False)
                _save_achat_piece_prices(request, maintenance)
                maintenance.refresh_total_facture()
                maintenance.statut = "en_cours"
                maintenance.save()
                maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Valorisation de diagnostic",
                    maintenance_label,
                    f"{request.user.username} a mis a jour l'achat et les prix du diagnostic {maintenance_label}.",
                )
                return redirect("achat_maintenances")
        messages.error(
            request,
            "Impossible d'enregistrer les prix. Verifiez les champs achat puis reessayez.",
        )
    else:
        form = MaintenanceAchatForm(instance=maintenance)
        if not can_edit_achat:
            _set_form_read_only(form)

    return _render_achat_form(
        request,
        "maintenance/modifier_maintenance_achat.html",
        form,
        maintenance=maintenance,
        read_only=not can_edit_achat,
        can_edit_achat=can_edit_achat,
    )


def terminer_maintenance(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    messages.info(request, "La cloture se fait maintenant par validation finale du DG.")
    return redirect("garage_maintenances")


def valider_maintenance_logistique(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "logistique":
        messages.error(request, "Seul le role Logistique peut faire cette validation.")
        return redirect("garage_maintenances")
    if not maintenance.is_pricing_complete():
        messages.error(request, "La logistique doit saisir tous les prix avant validation.")
        return redirect("garage_maintenances")
    if maintenance.is_validated_by_logistique():
        messages.info(request, "Cette fiche est deja validee par la logistique.")
        return redirect("garage_maintenances")

    maintenance.validation_logistique_at = timezone.now()
    maintenance.validation_logistique_by = request.user
    maintenance.save(update_fields=["validation_logistique_at", "validation_logistique_by"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Validation logistique",
        maintenance.reference,
        f"{request.user.username} a valide la fiche {maintenance.reference} au niveau logistique.",
    )
    return redirect("garage_maintenances")


def rejeter_maintenance_dga(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "dga":
        messages.error(request, "Seul le role DGA peut rejeter cette fiche.")
        return redirect("garage_maintenances")
    if not maintenance.is_validated_by_logistique():
        messages.error(request, "La validation logistique est requise avant le DGA.")
        return redirect("garage_maintenances")

    maintenance.statut = "refusee"
    if not maintenance.date_fin:
        maintenance.date_fin = timezone.now()
    maintenance.save(update_fields=["statut", "date_fin"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Rejet DGA",
        maintenance.reference,
        f"{request.user.username} a rejete la fiche {maintenance.reference} au niveau DGA.",
    )
    return redirect("garage_maintenances")


def valider_maintenance_dga(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "dga":
        messages.error(request, "Seul le role DGA peut faire cette validation.")
        return redirect("garage_maintenances")
    if not maintenance.is_validated_by_logistique():
        messages.error(request, "La validation logistique est requise avant le DGA.")
        return redirect("garage_maintenances")
    if maintenance.is_validated_by_dga():
        messages.info(request, "Cette fiche est deja validee par le DGA.")
        return redirect("garage_maintenances")

    maintenance.validation_dga_at = timezone.now()
    maintenance.validation_dga_by = request.user
    maintenance.save(update_fields=["validation_dga_at", "validation_dga_by"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Validation DGA",
        maintenance.reference,
        f"{request.user.username} a valide la fiche {maintenance.reference} au niveau DGA.",
    )
    return redirect("garage_maintenances")


def rejeter_maintenance_dg(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "directeur":
        messages.error(request, "Seul le role DG peut rejeter cette fiche.")
        return redirect("garage_maintenances")
    if not maintenance.is_validated_by_dga():
        messages.error(request, "La validation DGA est requise avant le DG.")
        return redirect("garage_maintenances")

    maintenance.statut = "annulee"
    if not maintenance.date_fin:
        maintenance.date_fin = timezone.now()
    maintenance.save(update_fields=["statut", "date_fin"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Rejet DG",
        maintenance.reference,
        f"{request.user.username} a rejete la fiche {maintenance.reference} au niveau DG.",
    )
    return redirect("garage_maintenances")


def valider_maintenance_dg(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "directeur":
        messages.error(request, "Seul le role DG peut faire cette validation.")
        return redirect("garage_maintenances")
    if not maintenance.is_validated_by_dga():
        messages.error(request, "La validation DGA est requise avant le DG.")
        return redirect("garage_maintenances")
    if maintenance.is_validated_by_dg():
        messages.info(request, "Cette fiche est deja validee par le DG.")
        return redirect("garage_maintenances")

    maintenance.validation_dg_at = timezone.now()
    maintenance.validation_dg_by = request.user
    maintenance.statut = "terminee"
    if not maintenance.date_fin:
        maintenance.date_fin = timezone.now()
    maintenance.save(update_fields=["validation_dg_at", "validation_dg_by", "statut", "date_fin"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Validation DG",
        maintenance.reference,
        f"{request.user.username} a valide la fiche {maintenance.reference} au niveau DG.",
    )
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
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut supprimer une fiche maintenance.")
        return redirect("garage_maintenances")
    maintenance = get_object_or_404(Maintenance, id=id)
    camion = maintenance.camion
    maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
    maintenance.delete()

    maintenance_active = camion.maintenances.filter(statut="en_cours").exists()
    if not maintenance_active and camion.etat == "au_garage":
        camion.etat = "disponible"
        camion.save(update_fields=["etat"])

    journaliser_action(
        request.user,
        "Maintenance",
        "Suppression de diagnostic",
        maintenance_label,
        f"{request.user.username} a supprime le diagnostic {maintenance_label}.",
    )
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


def ajouter_fournisseur_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = FournisseurForm(request.POST)
    if form.is_valid():
        fournisseur = form.save()
        return JsonResponse(
            {
                "success": True,
                "fournisseur": {
                    "id": fournisseur.id,
                    "label": str(fournisseur),
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_prestataire_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = PrestataireForm(request.POST)
    if form.is_valid():
        prestataire = form.save()
        return JsonResponse(
            {
                "success": True,
                "prestataire": {
                    "label": str(prestataire),
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def export_garage_xls(request):
    queryset, _ = _apply_maintenance_filters(request, _maintenance_queryset())
    return _export_maintenance_xls(queryset, "maintenance_garage")


def export_garage_pdf(request):
    queryset, _ = _apply_maintenance_filters(request, _maintenance_queryset())
    return _export_maintenance_pdf(queryset, "maintenance_garage")


def export_achat_xls(request):
    historique = request.GET.get("scope") == "historique"
    queryset = _maintenance_queryset()
    queryset = queryset.exclude(statut="en_cours") if historique else queryset.filter(statut="en_cours")
    queryset, _ = _apply_maintenance_filters(request, queryset)
    return _export_maintenance_xls(queryset, "maintenance_achat")


def export_achat_pdf(request):
    historique = request.GET.get("scope") == "historique"
    queryset = _maintenance_queryset()
    queryset = queryset.exclude(statut="en_cours") if historique else queryset.filter(statut="en_cours")
    queryset, _ = _apply_maintenance_filters(request, queryset)
    return _export_maintenance_pdf(queryset, "maintenance_achat")


liste_maintenances = role_required("logistique", "maintenancier", "directeur")(garage_maintenances)
garage_maintenances = role_required("logistique", "maintenancier", "dga", "directeur")(garage_maintenances)
achat_maintenances = role_required("logistique", "maintenancier", "dga", "directeur")(achat_maintenances)
fournisseurs_maintenance = role_required("logistique", "directeur")(fournisseurs_maintenance)
ajouter_fournisseur = role_required("logistique", "directeur")(ajouter_fournisseur)
modifier_fournisseur = role_required("logistique", "directeur")(modifier_fournisseur)
ajouter_maintenance_garage = role_required("logistique", "maintenancier", "directeur")(ajouter_maintenance_garage)
modifier_maintenance_garage = role_required("logistique", "maintenancier", "dga", "directeur")(modifier_maintenance_garage)
modifier_maintenance_achat = role_required("logistique", "maintenancier", "dga", "directeur")(modifier_maintenance_achat)
terminer_maintenance = role_required("logistique", "maintenancier", "directeur")(terminer_maintenance)
valider_maintenance_logistique = role_required("logistique")(valider_maintenance_logistique)
rejeter_maintenance_dga = role_required("dga")(rejeter_maintenance_dga)
valider_maintenance_dga = role_required("dga")(valider_maintenance_dga)
rejeter_maintenance_dg = role_required("directeur")(rejeter_maintenance_dg)
valider_maintenance_dg = role_required("directeur")(valider_maintenance_dg)
imprimer_maintenance = role_required("logistique", "maintenancier", "dga", "directeur")(imprimer_maintenance)
supprimer_maintenance = role_required("logistique", "maintenancier", "dga", "directeur")(supprimer_maintenance)
ajouter_type_maintenance_modal = role_required("logistique", "maintenancier", "directeur")(ajouter_type_maintenance_modal)
ajouter_fournisseur_modal = role_required("logistique", "directeur")(ajouter_fournisseur_modal)
ajouter_prestataire_modal = role_required("logistique", "directeur")(ajouter_prestataire_modal)
supprimer_fournisseur = role_required("logistique", "directeur")(supprimer_fournisseur)
export_garage_xls = role_required("logistique", "maintenancier", "dga", "directeur")(export_garage_xls)
export_garage_pdf = role_required("logistique", "maintenancier", "dga", "directeur")(export_garage_pdf)
export_achat_xls = role_required("logistique", "maintenancier", "dga", "directeur")(export_achat_xls)
export_achat_pdf = role_required("logistique", "maintenancier", "dga", "directeur")(export_achat_pdf)
