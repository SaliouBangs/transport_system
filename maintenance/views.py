from io import BytesIO
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.formats import date_format

from chauffeurs.models import Chauffeur
from camions.models import Camion
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, is_admin_user, role_required

from .forms import (
    ArticleStockForm,
    ArticleStockConversionFormSet,
    FournisseurForm,
    MaintenanceAchatForm,
    MaintenanceGarageForm,
    MaintenanceGarageLigneFormSet,
    MaintenancePaiementForm,
    MouvementStockForm,
    PrestataireForm,
    TypeMaintenanceForm,
)
from .models import (
    ArticleStock,
    Fournisseur,
    Maintenance,
    MaintenanceSousLigne,
    MouvementStock,
    Prestataire,
)


def _maintenance_queryset():
    _normalize_stock_only_workflow()
    return Maintenance.objects.select_related("camion", "fournisseur").prefetch_related(
        "lignes__type_maintenance",
        "lignes__sous_lignes__article_stock",
    )


def _normalize_stock_only_workflow():
    candidates = (
        Maintenance.objects.exclude(statut__in=["payee", "rejetee_dga", "rejetee_dg", "validee_stock"])
        .prefetch_related("lignes__sous_lignes")
    )
    updates = []
    for maintenance in candidates:
        if not maintenance.is_stock_only():
            continue
        if maintenance.validation_dg_at:
            target_status = "validee_stock"
        elif maintenance.validation_dga_at:
            target_status = "attente_dg"
        else:
            target_status = "attente_dga"
        if maintenance.statut != target_status:
            updates.append((maintenance.pk, target_status))

    for maintenance_id, target_status in updates:
        Maintenance.objects.filter(pk=maintenance_id).update(statut=target_status)


def _apply_maintenance_filters(request, queryset):
    q = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    statut = (request.GET.get("statut") or "").strip()

    if q:
        queryset = queryset.filter(
            Q(reference__icontains=q)
            | Q(numero_facture__icontains=q)
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
                maintenance.mode_paiement or "",
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
            "Mode paiement",
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
        "Mode paiement",
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


_UNITS = {
    0: "zero",
    1: "un",
    2: "deux",
    3: "trois",
    4: "quatre",
    5: "cinq",
    6: "six",
    7: "sept",
    8: "huit",
    9: "neuf",
    10: "dix",
    11: "onze",
    12: "douze",
    13: "treize",
    14: "quatorze",
    15: "quinze",
    16: "seize",
}
_TENS = {
    20: "vingt",
    30: "trente",
    40: "quarante",
    50: "cinquante",
    60: "soixante",
}


def _number_to_french(n):
    n = int(n)
    if n < 0:
        return "moins " + _number_to_french(-n)
    if n in _UNITS:
        return _UNITS[n]
    if n < 20:
        return "dix-" + _UNITS[n - 10]
    if n < 70:
        tens = (n // 10) * 10
        unit = n % 10
        base = _TENS[tens]
        if unit == 0:
            return base
        if unit == 1:
            return f"{base} et un"
        return f"{base}-{_number_to_french(unit)}"
    if n < 80:
        if n == 71:
            return "soixante et onze"
        return f"soixante-{_number_to_french(n - 60)}"
    if n < 100:
        if n == 80:
            return "quatre-vingts"
        return f"quatre-vingt-{_number_to_french(n - 80)}"
    if n < 1000:
        hundreds = n // 100
        rest = n % 100
        if hundreds == 1:
            prefix = "cent"
        else:
            prefix = f"{_number_to_french(hundreds)} cent"
        if rest == 0:
            return prefix
        return f"{prefix} {_number_to_french(rest)}"
    if n < 1_000_000:
        thousands = n // 1000
        rest = n % 1000
        if thousands == 1:
            prefix = "mille"
        else:
            prefix = f"{_number_to_french(thousands)} mille"
        if rest == 0:
            return prefix
        return f"{prefix} {_number_to_french(rest)}"
    millions = n // 1_000_000
    rest = n % 1_000_000
    prefix = "un million" if millions == 1 else f"{_number_to_french(millions)} millions"
    if rest == 0:
        return prefix
    return f"{prefix} {_number_to_french(rest)}"


def _amount_to_words(amount):
    amount = Decimal(amount or 0)
    entier = int(amount)
    decimals = int((amount - Decimal(entier)) * 100)
    words = _number_to_french(entier) + " francs guineens"
    if decimals:
        words += f" et {_number_to_french(decimals)} centimes"
    return words


def _format_amount(amount):
    amount = Decimal(amount or 0)
    quantized = amount.quantize(Decimal("0.01"))
    text = f"{quantized:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if text.endswith(",00"):
        return text[:-3]
    return text


def _format_step_date(value):
    if not value:
        return "-"
    if hasattr(value, "hour"):
        return date_format(value, "d/m/Y H:i")
    return date_format(value, "d/m/Y")


def _get_duplicate_facture_matches(numero_facture, maintenance_id=None):
    numero_facture = (numero_facture or "").strip()
    if not numero_facture:
        return []
    queryset = (
        Maintenance.objects.exclude(numero_facture="")
        .filter(numero_facture__iexact=numero_facture)
        .select_related("camion")
        .order_by("-date_creation")
    )
    if maintenance_id:
        queryset = queryset.exclude(pk=maintenance_id)
    return list(queryset[:5])


def _attach_subline_values(formset, request=None):
    for ligne_form in formset.forms:
        if request and request.method == "POST":
            ids = request.POST.getlist(f"subline-{ligne_form.prefix}-ids")
            labels = request.POST.getlist(f"subline-{ligne_form.prefix}-labels")
            quantities = request.POST.getlist(f"subline-{ligne_form.prefix}-quantites")
            article_ids = request.POST.getlist(f"subline-{ligne_form.prefix}-articles")
            values = [
                {
                    "id": ids[index] if index < len(ids) else "",
                    "libelle": labels[index] if index < len(labels) else "",
                    "quantite": quantities[index] if index < len(quantities) else "1",
                    "article_stock_id": article_ids[index] if index < len(article_ids) else "",
                    "prix_unitaire": "0",
                    "montant": "0",
                }
                for index in range(max(len(labels), len(quantities), len(ids), len(article_ids)))
            ]
        else:
            values = (
                list(
                    ligne_form.instance.sous_lignes.values(
                        "id",
                        "article_stock_id",
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
            {
                "id": "",
                "article_stock_id": "",
                "libelle": "",
                "quantite": "1",
                "prix_unitaire": "0",
                "montant": "0",
            }
        ]

    formset.empty_form.subline_values = [
        {
            "id": "",
            "article_stock_id": "",
            "libelle": "",
            "quantite": "1",
            "prix_unitaire": "0",
            "montant": "0",
        }
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
        article_ids = request.POST.getlist(f"subline-{ligne_form.prefix}-articles")

        kept_ids = []
        existing_by_id = {str(item.id): item for item in ligne.sous_lignes.all()}

        for index in range(max(len(labels), len(quantities), len(posted_ids), len(article_ids))):
            subline_id = posted_ids[index].strip() if index < len(posted_ids) and posted_ids[index] else ""
            label = labels[index].strip() if index < len(labels) and labels[index] else ""
            quantity_raw = quantities[index].strip() if index < len(quantities) and quantities[index] else "1"
            article_id = article_ids[index].strip() if index < len(article_ids) and article_ids[index] else ""
            article = ArticleStock.objects.filter(pk=article_id).first() if article_id else None
            if article and not label:
                label = article.libelle

            if not label:
                continue

            if subline_id and subline_id in existing_by_id:
                subline = existing_by_id[subline_id]
                if subline.mouvement_stock_id and (
                    str(subline.article_stock_id or "") != str(article.id if article else "")
                    or str(subline.quantite) != str(quantity_raw or "1")
                ):
                    raise ValidationError(
                        f"La piece '{subline.libelle}' a deja ete sortie du stock. "
                        "Seul l'administrateur peut corriger ce mouvement manuellement."
                    )
                subline.article_stock = article
                subline.libelle = label
                subline.quantite = quantity_raw or "1"
                subline.save()
                kept_ids.append(subline.id)
            elif allow_create:
                subline = MaintenanceSousLigne.objects.create(
                    maintenance_ligne=ligne,
                    article_stock=article,
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


def _issue_stock_for_maintenance(maintenance, user):
    pieces = list(
        MaintenanceSousLigne.objects.select_related("article_stock", "mouvement_stock")
        .filter(maintenance_ligne__maintenance=maintenance, article_stock__isnull=False)
    )
    if not pieces:
        return

    for piece in pieces:
        if piece.mouvement_stock_id:
            continue
        stock_disponible = piece.article_stock.quantite_stock or Decimal("0")
        if piece.quantite > stock_disponible:
            raise ValidationError(
                f"Stock insuffisant pour {piece.article_stock.libelle}. "
                f"Disponible: {_format_amount(stock_disponible)} {piece.article_stock.unite}. "
                f"Demande: {_format_amount(piece.quantite)}."
            )

    for piece in pieces:
        if piece.mouvement_stock_id:
            continue
        mouvement = MouvementStock.objects.create(
            article=piece.article_stock,
            type_mouvement="sortie",
            quantite=piece.quantite,
            quantite_saisie=piece.quantite,
            unite_saisie=piece.article_stock.unite,
            reference=maintenance.reference,
            observation=f"Consommation maintenance {maintenance.reference} - {piece.libelle}",
            date_mouvement=timezone.now(),
            created_by=user,
        )
        MaintenanceSousLigne.objects.filter(pk=piece.pk).update(mouvement_stock=mouvement)


def _save_achat_piece_prices(request, maintenance):
    for ligne in maintenance.lignes.prefetch_related("sous_lignes"):
        if ligne.sous_lignes.exists():
            for piece in ligne.sous_lignes.all():
                if piece.article_stock_id:
                    continue
                value = (request.POST.get(f"piece-price-{piece.id}") or "0").strip().replace(",", ".")
                piece.prix_unitaire = value or "0"
                piece.save()
        else:
            value = (request.POST.get(f"ligne-price-{ligne.id}") or "0").strip().replace(",", ".")
            ligne.prix_unitaire = value or "0"
            ligne.save()


def _maintenance_tabs_context(active_tab):
    return {"active_tab": active_tab}


def _can_manage_stock(user):
    return is_admin_user(user) or get_user_role(user) in {"logistique", "directeur"}


def _build_conversion_map(article, formset, principal_unit):
    conversions = {}
    if formset is not None:
        for form in formset.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            unite_source = (form.cleaned_data.get("unite_source") or "").strip().lower()
            quantite_equivalente = form.cleaned_data.get("quantite_equivalente")
            if unite_source and quantite_equivalente:
                conversions[unite_source] = Decimal(quantite_equivalente)
    if article is not None:
        for conversion in article.conversions.all():
            conversions.setdefault(conversion.unite_source.lower(), conversion.quantite_equivalente)
    conversions[(principal_unit or "").strip().lower()] = Decimal("1")
    return conversions


def _convert_to_principal_unit(quantity, source_unit, principal_unit, conversion_map):
    quantity = Decimal(quantity or 0)
    source_unit = (source_unit or principal_unit or "").strip().lower()
    principal_unit = (principal_unit or "").strip().lower()
    if source_unit == principal_unit:
        return quantity
    if source_unit in conversion_map:
        return quantity * Decimal(conversion_map[source_unit])
    raise ValidationError(
        {"unite_stock_saisie": f"Aucune conversion definie entre {source_unit} et {principal_unit}."}
    )


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
    historique = request.GET.get("scope") == "historique"
    maintenances_qs = _maintenance_queryset()
    if historique:
        maintenances_qs = maintenances_qs.filter(
            statut__in=["payee", "validee_stock", "rejetee_dga", "rejetee_dg"]
        )
    else:
        maintenances_qs = maintenances_qs.exclude(
            statut__in=["payee", "validee_stock", "rejetee_dga", "rejetee_dg"]
        )
    maintenances_qs, filter_values = _apply_maintenance_filters(request, maintenances_qs)
    maintenances = list(maintenances_qs)
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    for maintenance in maintenances:
        maintenance.pricing_complete = maintenance.is_pricing_complete()
        maintenance.can_terminate = False
        maintenance.can_reject_dga = (
            user_role == "dga"
            and maintenance.statut == "attente_dga"
        )
        maintenance.can_validate_dga = (
            user_role == "dga"
            and maintenance.statut == "attente_dga"
        )
        maintenance.can_reject_dg = (
            user_role == "directeur"
            and maintenance.statut == "attente_dg"
        )
        maintenance.can_validate_dg = (
            user_role == "directeur"
            and maintenance.statut == "attente_dg"
        )
        maintenance.can_enter_prices = (
            user_role == "logistique"
            and maintenance.statut == "attente_prix"
        )
        if maintenance.statut == "rejetee_dga":
            maintenance.validation_status_label = "Rejete par le DGA"
            maintenance.validation_status_variant = "danger"
        elif maintenance.statut == "rejetee_dg":
            maintenance.validation_status_label = "Rejete par le DG"
            maintenance.validation_status_variant = "danger"
        elif maintenance.statut == "payee":
            maintenance.validation_status_label = "Payee"
            maintenance.validation_status_variant = "ok"
        elif maintenance.statut == "validee_stock":
            maintenance.validation_status_label = "Validee stock"
            maintenance.validation_status_variant = "ok"
        elif maintenance.statut == "en_cours":
            maintenance.validation_status_label = "Diagnostic en cours"
            maintenance.validation_status_variant = "warning"
        elif maintenance.statut == "attente_prix":
            maintenance.validation_status_label = "En attente de saisie de prix"
            maintenance.validation_status_variant = "warning"
        elif maintenance.statut == "attente_dga":
            maintenance.validation_status_label = "En attente validation DGA"
            maintenance.validation_status_variant = "warning"
        elif maintenance.statut == "attente_dg":
            maintenance.validation_status_label = "En attente validation DG"
            maintenance.validation_status_variant = "warning"
        elif maintenance.statut == "attente_paiement":
            maintenance.validation_status_label = "En attente de paiement"
            maintenance.validation_status_variant = "warning"
        else:
            maintenance.validation_status_label = maintenance.get_statut_display()
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
            "historique": historique,
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
        maintenances = maintenances.exclude(
            Q(validation_dga_at__isnull=True) & Q(statut__in=["attente_prix", "attente_dga"])
        )
    else:
        maintenances = maintenances.filter(
            validation_dga_at__isnull=True,
            statut__in=["attente_prix", "attente_dga"],
        )
    maintenances, filter_values = _apply_maintenance_filters(request, maintenances)
    maintenances = list(maintenances)
    for maintenance in maintenances:
        maintenance.can_edit_prices = bool(
            can_edit_achat
            and maintenance.validation_dga_at is None
            and maintenance.statut in {"attente_prix", "attente_dga"}
        )
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


def paiements_maintenances(request):
    historique = request.GET.get("scope") == "historique"
    maintenances = _maintenance_queryset()
    if historique:
        maintenances = maintenances.filter(statut="payee")
    else:
        maintenances = maintenances.filter(statut="attente_paiement")
    maintenances, filter_values = _apply_maintenance_filters(request, maintenances)
    return render(
        request,
        "maintenance/paiements.html",
        {
            "maintenances": maintenances,
            "historique": historique,
            "filter_values": filter_values,
            "statut_choices": Maintenance.STATUT_CHOICES,
            "can_edit_paiement": True,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("paiements"),
        },
    )


def rapport_maintenances(request):
    maintenances_qs = _maintenance_queryset()
    q = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    statut = (request.GET.get("statut") or "").strip()

    if q:
        maintenances_qs = maintenances_qs.filter(
            Q(reference__icontains=q)
            | Q(camion__code_camion__icontains=q)
            | Q(camion__numero_tracteur__icontains=q)
            | Q(camion__numero_citerne__icontains=q)
            | Q(camion__chauffeur__nom__icontains=q)
            | Q(fournisseur__nom_fournisseur__icontains=q)
            | Q(prestataire__icontains=q)
            | Q(numero_facture__icontains=q)
        ).distinct()
    if date_from:
        maintenances_qs = maintenances_qs.filter(date_paiement__gte=date_from)
    if date_to:
        maintenances_qs = maintenances_qs.filter(date_paiement__lte=date_to)
    if statut:
        maintenances_qs = maintenances_qs.filter(statut=statut)

    filter_values = {
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "statut": statut,
    }
    maintenances = list(maintenances_qs)

    total_situations = len(maintenances)
    total_montant = sum((maintenance.total_facture or Decimal("0")) for maintenance in maintenances)
    total_payees = sum(1 for maintenance in maintenances if maintenance.statut == "payee")
    total_attente = sum(
        1
        for maintenance in maintenances
        if maintenance.statut in {"attente_prix", "attente_dga", "attente_dg", "attente_paiement"}
    )

    for maintenance in maintenances:
        maintenance.chauffeur_nom = (
            Chauffeur.objects.filter(camion=maintenance.camion)
            .values_list("nom", flat=True)
            .first()
            or "-"
        )
        diagnostics = []
        for ligne in maintenance.lignes.all():
            piece_labels = [piece.libelle for piece in ligne.sous_lignes.all()]
            diagnostic = f"{ligne.type_maintenance.libelle}: {ligne.libelle}"
            if piece_labels:
                diagnostic += f" ({', '.join(piece_labels)})"
            diagnostics.append(diagnostic)
        maintenance.diagnostics_resume = diagnostics

    return render(
        request,
        "maintenance/rapport.html",
        {
            "maintenances": maintenances,
            "filter_values": filter_values,
            "statut_choices": Maintenance.STATUT_CHOICES,
            "total_situations": total_situations,
            "total_montant": _format_amount(total_montant),
            "total_payees": total_payees,
            "total_attente": total_attente,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("rapports"),
        },
    )


def stock_maintenances(request):
    q = (request.GET.get("q") or "").strip()
    articles = ArticleStock.objects.select_related("fournisseur")
    if q:
        articles = articles.filter(
            Q(code_article__icontains=q)
            | Q(libelle__icontains=q)
            | Q(categorie__icontains=q)
            | Q(fournisseur__nom_fournisseur__icontains=q)
        )
    articles = list(articles.order_by("libelle"))
    recent_movements = list(
        MouvementStock.objects.select_related("article", "created_by")[:12]
    )
    maintenance_refs = {
        mouvement.reference
        for mouvement in recent_movements
        if (mouvement.reference or "").startswith("MAIN")
    }
    maintenance_by_ref = {
        maintenance.reference: maintenance
        for maintenance in Maintenance.objects.select_related("camion").filter(reference__in=maintenance_refs)
    }
    for mouvement in recent_movements:
        maintenance = maintenance_by_ref.get(mouvement.reference)
        if maintenance and maintenance.camion_id:
            mouvement.camion_display = maintenance.camion.numero_tracteur
            if maintenance.camion.numero_citerne:
                mouvement.camion_display += f" / {maintenance.camion.numero_citerne}"
        else:
            mouvement.camion_display = "-"
    consommation_par_camion = list(
        MaintenanceSousLigne.objects.filter(
            article_stock__isnull=False,
            mouvement_stock__isnull=False,
        )
        .values(
            "maintenance_ligne__maintenance__camion__code_camion",
            "maintenance_ligne__maintenance__camion__numero_tracteur",
            "maintenance_ligne__maintenance__camion__numero_citerne",
            "article_stock__libelle",
            "article_stock__unite",
        )
        .annotate(
            total_quantite=Sum("quantite"),
            total_maintenances=Count("maintenance_ligne__maintenance", distinct=True),
        )
        .order_by("-total_quantite", "article_stock__libelle")
    )
    total_articles = len(articles)
    articles_en_alerte = sum(1 for article in articles if article.en_alerte)
    valeur_stock = sum((article.valeur_stock for article in articles), Decimal("0"))
    can_manage_stock = _can_manage_stock(request.user)
    return render(
        request,
        "maintenance/stock.html",
        {
            "articles": articles,
            "recent_movements": recent_movements,
            "filter_values": {"q": q},
            "total_articles": total_articles,
            "articles_en_alerte": articles_en_alerte,
            "valeur_stock": _format_amount(valeur_stock),
            "can_manage_stock": can_manage_stock,
            "consommation_par_camion": consommation_par_camion,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("stock"),
        },
    )


def ajouter_article_stock(request):
    if request.method == "POST":
        form = ArticleStockForm(request.POST)
        formset = ArticleStockConversionFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    article = form.save(commit=False)
                    principal_unit = (form.cleaned_data.get("unite") or "").strip().lower()
                    source_unit = (form.cleaned_data.get("unite_stock_saisie") or principal_unit).strip().lower()
                    conversion_map = _build_conversion_map(None, formset, principal_unit)
                    article.quantite_stock = _convert_to_principal_unit(
                        form.cleaned_data.get("quantite_stock"),
                        source_unit,
                        principal_unit,
                        conversion_map,
                    )
                    article.save()
                    formset.instance = article
                    formset.save()
            except ValidationError as error:
                form.add_error("unite_stock_saisie", error.message_dict.get("unite_stock_saisie", [str(error)])[0] if hasattr(error, "message_dict") else str(error))
            else:
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Creation article de stock",
                    article.code_article,
                    f"{request.user.username} a ajoute l'article de stock {article.code_article} - {article.libelle}.",
                )
                messages.success(request, f"L'article {article.libelle} a ete ajoute au stock.")
                return redirect("stock_maintenances")
    else:
        form = ArticleStockForm(initial={"unite_stock_saisie": "piece"})
        formset = ArticleStockConversionFormSet()
    return render(
        request,
        "maintenance/ajouter_article_stock.html",
        {
            "form": form,
            "formset": formset,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("stock"),
        },
    )


def modifier_article_stock(request, id):
    article = get_object_or_404(ArticleStock, id=id)
    if request.method == "POST":
        form = ArticleStockForm(request.POST, instance=article)
        formset = ArticleStockConversionFormSet(request.POST, instance=article)
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    article = form.save(commit=False)
                    principal_unit = (form.cleaned_data.get("unite") or article.unite).strip().lower()
                    source_unit = (form.cleaned_data.get("unite_stock_saisie") or principal_unit).strip().lower()
                    conversion_map = _build_conversion_map(article, formset, principal_unit)
                    article.quantite_stock = _convert_to_principal_unit(
                        form.cleaned_data.get("quantite_stock"),
                        source_unit,
                        principal_unit,
                        conversion_map,
                    )
                    article.save()
                    formset.save()
            except ValidationError as error:
                form.add_error("unite_stock_saisie", error.message_dict.get("unite_stock_saisie", [str(error)])[0] if hasattr(error, "message_dict") else str(error))
            else:
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Modification article de stock",
                    article.code_article,
                    f"{request.user.username} a modifie l'article de stock {article.code_article}.",
                )
                messages.success(request, f"L'article {article.libelle} a ete mis a jour.")
                return redirect("stock_maintenances")
    else:
        form = ArticleStockForm(instance=article, initial={"unite_stock_saisie": article.unite})
        formset = ArticleStockConversionFormSet(instance=article)
    return render(
        request,
        "maintenance/modifier_article_stock.html",
        {
            "form": form,
            "formset": formset,
            "article": article,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("stock"),
        },
    )


@require_POST
def supprimer_article_stock(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut supprimer un article de stock.")
        return redirect("stock_maintenances")

    article = get_object_or_404(ArticleStock, id=id)
    if article.mouvements.exists() or article.maintenances_consommees.exists():
        messages.error(
            request,
            "Impossible de supprimer cet article car il a deja des mouvements ou des maintenances liees.",
        )
        return redirect("stock_maintenances")

    label = f"{article.code_article} - {article.libelle}"
    article.delete()
    journaliser_action(
        request.user,
        "Maintenance",
        "Suppression article de stock",
        label,
        f"{request.user.username} a supprime l'article de stock {label}.",
    )
    messages.success(request, f"L'article {label} a ete supprime.")
    return redirect("stock_maintenances")


def ajouter_mouvement_stock(request, article_id):
    article = get_object_or_404(ArticleStock, id=article_id)
    if request.method == "POST":
        form = MouvementStockForm(request.POST, article=article)
        form.instance.article = article
        if form.is_valid():
            mouvement = form.save(commit=False)
            mouvement.article = article
            mouvement.created_by = request.user
            mouvement.save()
            journaliser_action(
                request.user,
                "Maintenance",
                "Mouvement de stock",
                article.code_article,
                f"{request.user.username} a enregistre un mouvement {mouvement.get_type_mouvement_display().lower()} pour {article.code_article}.",
            )
            messages.success(request, f"Le mouvement de stock pour {article.libelle} a ete enregistre.")
            return redirect("stock_maintenances")
    else:
        form = MouvementStockForm(
            article=article,
            initial={
                "unite_saisie": article.unite,
            },
        )
    return render(
        request,
        "maintenance/ajouter_mouvement_stock.html",
        {
            "form": form,
            "article": article,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("stock"),
        },
    )


def export_rapport_maintenances_xls(request):
    try:
        from openpyxl import Workbook
    except ImportError:
        return HttpResponse(
            "Le module openpyxl n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    queryset = _maintenance_queryset()
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
            | Q(fournisseur__nom_fournisseur__icontains=q)
            | Q(prestataire__icontains=q)
            | Q(numero_facture__icontains=q)
        ).distinct()
    if date_from:
        queryset = queryset.filter(date_paiement__gte=date_from)
    if date_to:
        queryset = queryset.filter(date_paiement__lte=date_to)
    if statut:
        queryset = queryset.filter(statut=statut)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rapport maintenance"
    sheet.append(
        [
            "Reference",
            "Code camion",
            "Numero tracteur",
            "Numero citerne",
            "Chauffeur",
            "Date entree garage",
            "Date sortie",
            "Kilometrage entree",
            "Kilometrage sortie",
            "Diagnostics",
            "Fournisseur",
            "Prestataire",
            "Numero facture",
            "Total facture",
            "Statut",
            "Validation logistique",
            "Validation DGA",
            "Validation DG",
            "Date paiement",
            "Observation",
        ]
    )

    for maintenance in queryset:
        chauffeur_nom = (
            Chauffeur.objects.filter(camion=maintenance.camion)
            .values_list("nom", flat=True)
            .first()
            or ""
        )
        diagnostics = []
        for ligne in maintenance.lignes.all():
            piece_labels = [piece.libelle for piece in ligne.sous_lignes.all()]
            diagnostic = f"{ligne.type_maintenance.libelle}: {ligne.libelle}"
            if piece_labels:
                diagnostic += f" ({', '.join(piece_labels)})"
            diagnostics.append(diagnostic)
        sheet.append(
            [
                maintenance.reference,
                maintenance.camion.code_camion,
                maintenance.camion.numero_tracteur,
                maintenance.camion.numero_citerne or "",
                chauffeur_nom,
                maintenance.date_debut.strftime("%d/%m/%Y %H:%M") if maintenance.date_debut else "",
                maintenance.date_fin.strftime("%d/%m/%Y %H:%M") if maintenance.date_fin else "",
                maintenance.kilometrage_entree or "",
                maintenance.kilometrage_sortie or "",
                " | ".join(diagnostics),
                str(maintenance.fournisseur or ""),
                maintenance.prestataire or "",
                maintenance.numero_facture or "",
                str(maintenance.total_facture or ""),
                maintenance.get_statut_display(),
                maintenance.validation_logistique_at.strftime("%d/%m/%Y %H:%M") if maintenance.validation_logistique_at else "",
                maintenance.validation_dga_at.strftime("%d/%m/%Y %H:%M") if maintenance.validation_dga_at else "",
                maintenance.validation_dg_at.strftime("%d/%m/%Y %H:%M") if maintenance.validation_dg_at else "",
                maintenance.date_paiement.strftime("%d/%m/%Y") if maintenance.date_paiement else "",
                maintenance.observation or "",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_maintenance_complet.xlsx"'
    workbook.save(response)
    return response


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
            **_maintenance_tabs_context("fournisseurs"),
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
            **_maintenance_tabs_context("fournisseurs"),
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
            **_maintenance_tabs_context("fournisseurs"),
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
            "articles_stock_catalog": ArticleStock.objects.order_by("libelle"),
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
            "duplicate_facture_matches": _get_duplicate_facture_matches(
                form["numero_facture"].value() if "numero_facture" in form.fields else context["maintenance"].numero_facture,
                maintenance_id=context["maintenance"].id,
            ),
            "is_admin_maintenance": is_admin_user(request.user),
            "can_edit_achat": context.get("can_edit_achat", False),
            **_maintenance_tabs_context("achat"),
            **context,
        },
    )


def _render_paiement_form(request, template_name, form, **context):
    preview_context = _build_validation_preview_context(context["maintenance"])
    return render(
        request,
        template_name,
        {
            "form": form,
            "is_admin_maintenance": is_admin_user(request.user),
            **_maintenance_tabs_context("paiements"),
            **preview_context,
            **context,
        },
    )


def _build_validation_preview_context(maintenance):
    chauffeur = (
        Chauffeur.objects.filter(camion=maintenance.camion)
        .values_list("nom", flat=True)
        .first()
    )
    validation_steps = [
        {
            "label": "Validation logistique",
            "status": (
                "Non requise pour stock interne"
                if maintenance.is_stock_only() and not maintenance.validation_logistique_at
                else "Valide par la logistique"
                if maintenance.validation_logistique_at
                else "En attente de validation logistique"
            ),
            "variant": (
                "neutral"
                if maintenance.is_stock_only() and not maintenance.validation_logistique_at
                else "ok"
                if maintenance.validation_logistique_at
                else "pending"
            ),
            "by": maintenance.validation_logistique_by.get_full_name() or maintenance.validation_logistique_by.username
            if maintenance.validation_logistique_by
            else "-",
            "at": maintenance.validation_logistique_at,
            "at_display": _format_step_date(maintenance.validation_logistique_at),
        },
        {
            "label": "Validation DGA",
            "status": (
                "Rejete par le DGA"
                if maintenance.statut == "rejetee_dga"
                else "Valide par le DGA"
                if maintenance.validation_dga_at
                else "En attente de validation DGA"
            ),
            "variant": (
                "danger"
                if maintenance.statut == "rejetee_dga"
                else "ok"
                if maintenance.validation_dga_at
                else "pending"
            ),
            "by": maintenance.validation_dga_by.get_full_name() or maintenance.validation_dga_by.username
            if maintenance.validation_dga_by
            else "-",
            "at": maintenance.validation_dga_at,
            "at_display": _format_step_date(maintenance.validation_dga_at),
        },
        {
            "label": "Validation DG",
            "status": (
                "Rejete par le DG"
                if maintenance.statut == "rejetee_dg"
                else "Valide par le DG"
                if maintenance.validation_dg_at
                else "En attente de validation DG"
            ),
            "variant": (
                "danger"
                if maintenance.statut == "rejetee_dg"
                else "ok"
                if maintenance.validation_dg_at
                else "pending"
            ),
            "by": maintenance.validation_dg_by.get_full_name() or maintenance.validation_dg_by.username
            if maintenance.validation_dg_by
            else "-",
            "at": maintenance.validation_dg_at,
            "at_display": _format_step_date(maintenance.validation_dg_at),
        },
        {
            "label": "Paiement",
            "status": (
                "Non applicable (stock interne)"
                if maintenance.statut == "validee_stock"
                else
                "Paiement effectue"
                if maintenance.statut == "payee"
                else "En attente de paiement"
                if maintenance.statut == "attente_paiement"
                else "Non disponible dans le circuit actuel"
            ),
            "variant": (
                "neutral"
                if maintenance.statut == "validee_stock"
                else
                "ok"
                if maintenance.statut == "payee"
                else "pending"
                if maintenance.statut == "attente_paiement"
                else "neutral"
            ),
            "by": "-",
            "at": maintenance.date_paiement,
            "at_display": _format_step_date(maintenance.date_paiement),
        },
    ]
    return {
        "maintenance": maintenance,
        "piece_rows": _attach_achat_piece_rows(maintenance),
        "montant_en_lettres": _amount_to_words(maintenance.total_facture),
        "montant_total_formatte": _format_amount(maintenance.total_facture),
        "chauffeur_nom": chauffeur or "-",
        "validation_steps": validation_steps,
    }


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
                maintenance.statut = "attente_prix"
                maintenance.save()
                formset.instance = maintenance
                formset.save()
                _save_subline_items(request, formset)
                maintenance.refresh_total_facture()
                maintenance.statut = "attente_dga" if maintenance.is_stock_only() else "attente_prix"
                maintenance.save(update_fields=["statut"])
                maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Creation de diagnostic",
                    maintenance_label,
                    f"{request.user.username} a cree le diagnostic {maintenance_label} pour le camion {maintenance.camion}.",
                )
            messages.success(
                request,
                (
                    f"Le diagnostic {maintenance.reference} a ete enregistre et transmis a la validation DGA."
                    if maintenance.is_stock_only()
                    else f"Le diagnostic {maintenance.reference} a ete enregistre et transmis a la saisie des prix."
                ),
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
    if maintenance.statut in {"payee", "validee_stock"}:
        messages.info(request, "Cette fiche finalisee est disponible dans l'historique garage.")
        return redirect("/maintenance/garage/?scope=historique")
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    can_edit_diagnostic = is_admin or (user_role == "maintenancier" and maintenance.statut in {"en_cours", "attente_prix"})
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
                maintenance.statut = "attente_dga" if maintenance.is_stock_only() else "attente_prix"
                maintenance.save(update_fields=["statut"])
                maintenance_label = maintenance.reference or f"Diagnostic #{maintenance.id}"
                journaliser_action(
                    request.user,
                    "Maintenance",
                    "Modification de diagnostic",
                    maintenance_label,
                    f"{request.user.username} a modifie le diagnostic {maintenance_label}.",
                )
            messages.success(
                request,
                (
                    f"Le diagnostic {maintenance.reference} a ete mis a jour et transmis a la validation DGA."
                    if maintenance.is_stock_only()
                    else f"Le diagnostic {maintenance.reference} a ete mis a jour et transmis a la saisie des prix."
                ),
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
    if maintenance.statut in {"payee", "validee_stock"}:
        messages.info(request, "Cette fiche finalisee est disponible dans l'historique achat.")
        return redirect("/maintenance/achat/?scope=historique")
    user_role = get_user_role(request.user)
    is_admin = is_admin_user(request.user)
    can_edit_achat = is_admin or (
        user_role == "logistique"
        and maintenance.validation_dga_at is None
        and maintenance.statut in {"attente_prix", "attente_dga", "rejetee_dga"}
    )
    if request.method == "POST":
        if not can_edit_achat:
            messages.error(request, "Seuls la logistique et l'administrateur peuvent modifier les achats avant decision finale.")
            return redirect("achat_maintenances")
        post_data = request.POST.copy()
        if not is_admin:
            post_data["statut"] = maintenance.statut
        post_data["prestataire"] = (post_data.get("prestataire_search") or post_data.get("prestataire") or "").strip()
        form = MaintenanceAchatForm(post_data, request.FILES, instance=maintenance)
        if form.is_valid():
            with transaction.atomic():
                maintenance = form.save(commit=False)
                maintenance.statut = maintenance.statut or "attente_prix"
                maintenance.save()
                _save_achat_piece_prices(request, maintenance)
                maintenance.refresh_total_facture()
                maintenance.validation_logistique_at = timezone.now()
                maintenance.validation_logistique_by = request.user
                maintenance.statut = "attente_dga"
                maintenance.save(
                    update_fields=[
                        "validation_logistique_at",
                        "validation_logistique_by",
                        "statut",
                    ]
                )
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
    if maintenance.statut != "en_cours":
        messages.info(request, "Ce diagnostic est deja transmis.")
        return redirect("garage_maintenances")

    maintenance.statut = "attente_prix"
    maintenance.save(update_fields=["statut"])
    journaliser_action(
        request.user,
        "Maintenance",
        "Transmission diagnostic",
        maintenance.reference,
        f"{request.user.username} a termine et transmis le diagnostic {maintenance.reference}.",
    )
    messages.success(request, "Le diagnostic a ete transmis pour saisie des prix.")
    return redirect("garage_maintenances")


def valider_maintenance_logistique(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    messages.info(request, "La validation logistique est maintenant automatique apres la saisie des prix.")
    return redirect("garage_maintenances")


def rejeter_maintenance_dga(request, id):
    if request.method != "POST":
        return redirect("garage_maintenances")

    maintenance = get_object_or_404(Maintenance, id=id)
    if get_user_role(request.user) != "dga":
        messages.error(request, "Seul le role DGA peut rejeter cette fiche.")
        return redirect("garage_maintenances")
    if maintenance.statut != "attente_dga":
        messages.error(request, "Cette fiche n'est pas en attente de validation DGA.")
        return redirect("garage_maintenances")

    maintenance.statut = "rejetee_dga"
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
    if maintenance.statut != "attente_dga":
        messages.error(request, "Cette fiche n'est pas en attente de validation DGA.")
        return redirect("garage_maintenances")
    if maintenance.is_validated_by_dga() and maintenance.statut != "attente_dga":
        messages.info(request, "Cette fiche est deja validee par le DGA.")
        return redirect("garage_maintenances")

    maintenance.validation_dga_at = timezone.now()
    maintenance.validation_dga_by = request.user
    maintenance.statut = "attente_dg"
    maintenance.save(update_fields=["validation_dga_at", "validation_dga_by", "statut"])
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
    if maintenance.statut != "attente_dg":
        messages.error(request, "Cette fiche n'est pas en attente de validation DG.")
        return redirect("garage_maintenances")

    maintenance.statut = "rejetee_dg"
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
    if maintenance.statut != "attente_dg":
        messages.error(request, "Cette fiche n'est pas en attente de validation DG.")
        return redirect("garage_maintenances")
    if maintenance.is_validated_by_dg() and maintenance.statut != "attente_dg":
        messages.info(request, "Cette fiche est deja validee par le DG.")
        return redirect("garage_maintenances")

    try:
        with transaction.atomic():
            _issue_stock_for_maintenance(maintenance, request.user)
            maintenance.validation_dg_at = timezone.now()
            maintenance.validation_dg_by = request.user
            maintenance.statut = "validee_stock" if maintenance.is_stock_only() else "attente_paiement"
            if not maintenance.date_fin:
                maintenance.date_fin = timezone.now()
            maintenance.save(update_fields=["validation_dg_at", "validation_dg_by", "statut", "date_fin"])
    except ValidationError as error:
        messages.error(request, str(error))
        return redirect("garage_maintenances")

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
    context = _build_validation_preview_context(maintenance)
    chauffeur = Chauffeur.objects.filter(camion=maintenance.camion).first()
    for row in context["piece_rows"]:
        if row["pieces"]:
            for piece in row["pieces"]:
                piece.prix_unitaire_affiche = _format_amount(piece.prix_unitaire)
                piece.montant_affiche = _format_amount(piece.montant)
        else:
            row["ligne"].prix_unitaire_affiche = _format_amount(row["ligne"].prix_unitaire)
            row["ligne"].montant_affiche = _format_amount(row["ligne"].montant)
    return render(
        request,
        "maintenance/imprimer_maintenance.html",
        {
            **context,
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


def modifier_maintenance_paiement(request, id):
    maintenance = get_object_or_404(Maintenance, id=id)
    can_edit_paiement = is_admin_user(request.user) or get_user_role(request.user) in ("comptable", "caissiere", "directeur")
    if request.method == "POST":
        if not can_edit_paiement or maintenance.statut != "attente_paiement":
            messages.error(request, "Cette fiche n'est pas disponible pour le paiement.")
            return redirect("paiements_maintenances")
        form = MaintenancePaiementForm(request.POST, instance=maintenance)
        if form.is_valid():
            maintenance = form.save(commit=False)
            maintenance.statut = "payee"
            maintenance.save()
            journaliser_action(
                request.user,
                "Maintenance",
                "Paiement maintenance",
                maintenance.reference,
                f"{request.user.username} a enregistre le paiement de la fiche {maintenance.reference}.",
            )
            messages.success(request, f"Le paiement de la fiche {maintenance.reference} a ete enregistre.")
            return redirect("paiements_maintenances")
        messages.error(request, "Impossible d'enregistrer le paiement. Verifiez les champs.")
    else:
        form = MaintenancePaiementForm(instance=maintenance)
        if not can_edit_paiement or maintenance.statut != "attente_paiement":
            _set_form_read_only(form)

    return _render_paiement_form(
        request,
        "maintenance/modifier_maintenance_paiement.html",
        form,
        maintenance=maintenance,
        can_edit_paiement=can_edit_paiement and maintenance.statut == "attente_paiement",
        historique=maintenance.statut == "payee",
    )


def apercu_validation_maintenance(request, id):
    maintenance = get_object_or_404(_maintenance_queryset(), id=id)
    user_role = get_user_role(request.user)
    if user_role == "dga" and maintenance.statut != "attente_dga":
        messages.error(request, "Cette fiche n'est pas en attente de validation DGA.")
        return redirect("garage_maintenances")
    if user_role == "directeur" and maintenance.statut != "attente_dg":
        messages.error(request, "Cette fiche n'est pas en attente de validation DG.")
        return redirect("garage_maintenances")
    return render(
        request,
        "maintenance/apercu_validation_maintenance.html",
        {
            **_build_validation_preview_context(maintenance),
            "user_role": user_role,
            **_maintenance_tabs_context("garage"),
        },
    )


def export_garage_xls(request):
    historique = request.GET.get("scope") == "historique"
    queryset = _maintenance_queryset()
    queryset = queryset.filter(statut="payee") if historique else queryset.exclude(statut="payee")
    queryset, _ = _apply_maintenance_filters(request, queryset)
    return _export_maintenance_xls(queryset, "maintenance_garage")


def export_garage_pdf(request):
    historique = request.GET.get("scope") == "historique"
    queryset = _maintenance_queryset()
    queryset = queryset.filter(statut="payee") if historique else queryset.exclude(statut="payee")
    queryset, _ = _apply_maintenance_filters(request, queryset)
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
garage_maintenances = role_required("logistique", "maintenancier", "dga", "directeur", "invite", "controleur")(garage_maintenances)
achat_maintenances = role_required("logistique", "directeur", "controleur")(achat_maintenances)
paiements_maintenances = role_required("comptable", "caissiere", "directeur")(paiements_maintenances)
modifier_maintenance_paiement = role_required("comptable", "caissiere", "directeur")(modifier_maintenance_paiement)
stock_maintenances = role_required("logistique", "maintenancier", "dga", "directeur", "comptable", "invite", "controleur")(stock_maintenances)
ajouter_article_stock = role_required("logistique", "directeur")(ajouter_article_stock)
modifier_article_stock = role_required("logistique", "directeur")(modifier_article_stock)
ajouter_mouvement_stock = role_required("logistique", "directeur")(ajouter_mouvement_stock)
fournisseurs_maintenance = role_required("logistique", "directeur")(fournisseurs_maintenance)
ajouter_fournisseur = role_required("logistique", "directeur")(ajouter_fournisseur)
modifier_fournisseur = role_required("logistique", "directeur")(modifier_fournisseur)
ajouter_maintenance_garage = role_required("logistique", "maintenancier", "directeur")(ajouter_maintenance_garage)
modifier_maintenance_garage = role_required("logistique", "maintenancier")(modifier_maintenance_garage)
modifier_maintenance_achat = role_required("logistique", "directeur", "controleur")(modifier_maintenance_achat)
terminer_maintenance = role_required("logistique", "directeur")(terminer_maintenance)
valider_maintenance_logistique = role_required("logistique")(valider_maintenance_logistique)
rejeter_maintenance_dga = role_required("dga")(rejeter_maintenance_dga)
valider_maintenance_dga = role_required("dga")(valider_maintenance_dga)
rejeter_maintenance_dg = role_required("directeur")(rejeter_maintenance_dg)
valider_maintenance_dg = role_required("directeur")(valider_maintenance_dg)
apercu_validation_maintenance = role_required("dga", "directeur")(apercu_validation_maintenance)
imprimer_maintenance = role_required("logistique", "maintenancier", "dga", "directeur", "comptable", "caissiere", "invite", "controleur")(imprimer_maintenance)
supprimer_maintenance = role_required("logistique", "maintenancier", "dga", "directeur")(supprimer_maintenance)
ajouter_type_maintenance_modal = role_required("logistique", "maintenancier", "directeur")(ajouter_type_maintenance_modal)
ajouter_fournisseur_modal = role_required("logistique", "directeur")(ajouter_fournisseur_modal)
ajouter_prestataire_modal = role_required("logistique", "directeur")(ajouter_prestataire_modal)
supprimer_fournisseur = role_required("logistique", "directeur")(supprimer_fournisseur)
export_garage_xls = role_required("logistique", "maintenancier", "dga", "directeur")(export_garage_xls)
export_garage_pdf = role_required("logistique", "maintenancier", "dga", "directeur")(export_garage_pdf)
export_achat_xls = role_required("logistique", "directeur")(export_achat_xls)
export_achat_pdf = role_required("logistique", "directeur")(export_achat_pdf)
rapport_maintenances = role_required("comptable", "caissiere", "logistique", "maintenancier", "dga", "directeur", "invite", "controleur")(rapport_maintenances)
export_rapport_maintenances_xls = role_required("comptable", "caissiere", "logistique", "maintenancier", "dga", "directeur", "invite", "controleur")(export_rapport_maintenances_xls)
