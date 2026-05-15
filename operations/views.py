from datetime import date
from io import BytesIO
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Sum
from django.db.models import Value
from django.db.models.functions import Replace
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from camions.models import Camion
from commandes.models import Commande
from depenses.models import Depense
from utilisateurs.permissions import role_required
from utilisateurs.permissions import get_user_role

from .forms import (
    ComptableOperationForm,
    DepotForm,
    FacturationOperationForm,
    LogistiqueOperationForm,
    LogisticienOperationForm,
    OperationForm,
    ProduitForm,
    RegimeDouanierForm,
    SommierForm,
)
from .models import HistoriqueAffectationOperation, Operation, Produit, Sommier


TVA_RATE = Decimal("0.18")

ETATS_TRANSITAIRE_RECEPTION = {"attente_reception_transitaire"}
ETATS_TRANSITAIRE_TRAITEMENT = {"transmis", "declare", "liquide", "attente_reception_logistique"}
ETATS_TRANSITAIRE_HISTORIQUE = {"liquide_logistique", "liquide_chauffeur", "charge", "livre"}
ETATS_LOGISTIQUE_RECEPTION = {"attente_reception_logistique"}
ETATS_LOGISTIQUE_TRAITEMENT = {"liquide_logistique", "liquide_chauffeur", "charge", "livre"}
ETATS_CHEF_CHAUFFEUR = {"liquide_chauffeur", "charge"}
ETATS_CHEF_CHAUFFEUR_HISTORIQUE = {"livre"}


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


def _comptable_operation_can_edit(operation):
    return (
        operation.etat_bon in {"initie", "attente_reception_transitaire"}
        and not operation.date_reception_transitaire
    )


def _get_comptable_editable_allocations(commande):
    if not commande:
        return Operation.objects.none()
    return (
        commande.operations.select_related("sommier")
        .filter(remplace_par__isnull=True)
        .filter(etat_bon__in={"initie", "attente_reception_transitaire"}, date_reception_transitaire__isnull=True)
        .order_by("date_creation", "id")
    )


def _restore_operation_sommier_stock(operation):
    if operation.sommier_id and operation.stock_sommier_deduit:
        sommier = Sommier.objects.select_for_update().filter(id=operation.sommier_id).first()
        if sommier:
            sommier.quantite_disponible = Decimal(sommier.quantite_disponible or 0) + Decimal(operation.quantite or 0)
            sommier.save(update_fields=["quantite_disponible"])


_UNITS_FR = {
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
_TENS_FR = {
    20: "vingt",
    30: "trente",
    40: "quarante",
    50: "cinquante",
    60: "soixante",
}


def _number_to_french_words(value):
    n = int(value)
    if n < 0:
        return "moins " + _number_to_french_words(-n)
    if n in _UNITS_FR:
        return _UNITS_FR[n]
    if n < 20:
        return "dix-" + _UNITS_FR[n - 10]
    if n < 70:
        tens = (n // 10) * 10
        unit = n % 10
        base = _TENS_FR[tens]
        if unit == 0:
            return base
        if unit == 1:
            return f"{base} et un"
        return f"{base}-{_number_to_french_words(unit)}"
    if n < 80:
        if n == 71:
            return "soixante et onze"
        return f"soixante-{_number_to_french_words(n - 60)}"
    if n < 100:
        if n == 80:
            return "quatre-vingts"
        return f"quatre-vingt-{_number_to_french_words(n - 80)}"
    if n < 1000:
        hundreds = n // 100
        rest = n % 100
        prefix = "cent" if hundreds == 1 else f"{_number_to_french_words(hundreds)} cent"
        return prefix if rest == 0 else f"{prefix} {_number_to_french_words(rest)}"
    if n < 1_000_000:
        thousands = n // 1000
        rest = n % 1000
        prefix = "mille" if thousands == 1 else f"{_number_to_french_words(thousands)} mille"
        return prefix if rest == 0 else f"{prefix} {_number_to_french_words(rest)}"
    millions = n // 1_000_000
    rest = n % 1_000_000
    prefix = "un million" if millions == 1 else f"{_number_to_french_words(millions)} millions"
    return prefix if rest == 0 else f"{prefix} {_number_to_french_words(rest)}"


def _amount_to_french_words(amount):
    amount = Decimal(amount or 0)
    entier = int(amount)
    decimals = int((amount - Decimal(entier)) * 100)
    words = _number_to_french_words(entier) + " francs guineens"
    if decimals:
        words += f" et {_number_to_french_words(decimals)} centimes"
    return words


def _decrement_sommier_stock_on_liquidation(operation):
    if operation.stock_sommier_deduit:
        return
    if not operation.sommier_id:
        raise ValidationError({"__all__": ["Aucun navire n'est selectionne sur ce BL. La comptabilite doit d'abord choisir le sommier."]})

    with transaction.atomic():
        sommier = Sommier.objects.select_for_update().get(pk=operation.sommier_id)
        quantite = Decimal(operation.quantite or 0)
        if operation.produit_id and sommier.produit_id != operation.produit_id:
            raise ValidationError({"__all__": ["Le navire selectionne ne correspond pas au produit de ce BL."]})
        if Decimal(sommier.quantite_disponible or 0) < quantite:
            raise ValidationError(
                {"__all__": [f"Le navire {sommier.reference_navire} n'a pas assez de stock disponible pour cette liquidation."]}
            )
        sommier.quantite_disponible = Decimal(sommier.quantite_disponible or 0) - quantite
        sommier.save(update_fields=["quantite_disponible"])
        operation.stock_sommier_deduit = True


def _facture_unit_price(operation):
    if operation.commande and operation.commande.prix_negocie is not None:
        return operation.commande.prix_negocie
    if operation.montant_facture is not None and operation.quantite:
        try:
            return Decimal(operation.montant_facture) / Decimal(operation.quantite)
        except Exception:
            return Decimal("0")
    return Decimal("0")


def _facture_totals(operation, avec_tva=False, utiliser_quantite_livree=False):
    quantite_source = operation.quantite_livree if utiliser_quantite_livree and operation.quantite_livree is not None else operation.quantite
    quantite = Decimal(quantite_source or 0)
    prix_unitaire = _facture_unit_price(operation)
    montant_ht = quantite * prix_unitaire
    montant_tva = (montant_ht * TVA_RATE).quantize(Decimal("0.01")) if avec_tva else Decimal("0.00")
    montant_ttc = montant_ht + montant_tva
    return {
        "quantite": quantite,
        "prix_unitaire": prix_unitaire,
        "montant_ht": montant_ht.quantize(Decimal("0.01")),
        "montant_tva": montant_tva,
        "montant_ttc": montant_ttc.quantize(Decimal("0.01")),
    }


def _format_amount(value):
    value = Decimal(value or 0).quantize(Decimal("0.01"))
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    if text.endswith(",00"):
        return text[:-3]
    return text


def _annotate_commande_groups(operations):
    grouped = {}
    ordered_keys = []
    for operation in operations:
        key = operation.commande_id or f"operation-{operation.id}"
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(operation)

    for key in ordered_keys:
        items = grouped[key]
        rowspan = len(items)
        is_multi = rowspan > 1 and items[0].commande_id
        for index, item in enumerate(items):
            item.commande_group_start = index == 0
            item.commande_group_rowspan = rowspan if index == 0 else 0
            item.commande_group_is_multi = is_multi
            item.commande_group_count = rowspan


def _apply_logistique_group_expense_display(operations):
    grouped = {}
    ordered_keys = []
    for operation in operations:
        key = operation.commande_id or f"operation-{operation.id}"
        if key not in grouped:
            grouped[key] = []
            ordered_keys.append(key)
        grouped[key].append(operation)

    for key in ordered_keys:
        items = grouped[key]
        for item in items:
            depenses = list(getattr(item, "depenses_chargement_items", []))
            item.depenses_bl_total = sum(
                (Decimal(depense.montant_total or depense.montant_estime or 0)
                 for depense in depenses if depense.portee_chargement == Depense.PORTEE_BL),
                Decimal("0"),
            )
            item.depenses_commande_total = sum(
                (Decimal(depense.montant_total or depense.montant_estime or 0)
                 for depense in depenses if depense.portee_chargement == Depense.PORTEE_COMMANDE),
                Decimal("0"),
            )
            item.depenses_display_total = item.depenses_bl_total
            item.depenses_is_shared_note = False
            item.depenses_shared_note = ""

        if len(items) <= 1 or not items[0].commande_id:
            for item in items:
                item.depenses_display_total += item.depenses_commande_total
                item.depenses_display_amount = (
                    _format_amount(item.depenses_display_total) + " GNF"
                    if item.depenses_display_total > 0
                    else ""
                )
            continue

        for index, item in enumerate(items):
            if index == 0:
                item.depenses_display_total += item.depenses_commande_total
            elif item.depenses_commande_total > 0:
                item.depenses_is_shared_note = True
                item.depenses_shared_note = "Depense commune commande"
            item.depenses_display_amount = (
                _format_amount(item.depenses_display_total) + " GNF"
                if item.depenses_display_total > 0
                else ""
            )


def _commande_sibling_operations(operation, expected_state=None):
    if not operation.commande_id:
        queryset = Operation.objects.filter(id=operation.id)
    else:
        queryset = Operation.objects.filter(
            commande_id=operation.commande_id,
            remplace_par__isnull=True,
        )
    if expected_state:
        queryset = queryset.filter(etat_bon=expected_state)
    return queryset.order_by("date_creation", "id")


def _depenses_chargement_queryset_for_operation(operation):
    base_queryset = Depense.objects.filter(source_depense=Depense.SOURCE_CHARGEMENT)
    if operation.commande_id:
        return base_queryset.filter(
            Q(operation_id=operation.id, portee_chargement=Depense.PORTEE_BL)
            | Q(commande_id=operation.commande_id, portee_chargement=Depense.PORTEE_COMMANDE)
        ).distinct()
    return base_queryset.filter(operation_id=operation.id)


def _push_validation_errors(request, exc):
    if hasattr(exc, "message_dict"):
        for errors in exc.message_dict.values():
            for error in errors:
                messages.error(request, error)
    else:
        for error in exc.messages:
            messages.error(request, error)


def _operation_is_locked_after_charge(operation):
    return bool(operation.date_bons_charges or operation.etat_bon in {"charge", "livre"})


def _operation_status_label(operation):
    if operation.etat_bon == "attente_reception_logistique":
        return "Liquide (en attente de validation de reception logistique)"
    if operation.date_bon_retour:
        return "Livre / Retourne"
    return operation.get_etat_bon_display()


def _build_facture_pdf(operation, avec_tva=False, utiliser_quantite_livree=False):
    try:
        import os
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError:
        return HttpResponse(
            "Le module reportlab n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    totals = _facture_totals(operation, avec_tva=avec_tva, utiliser_quantite_livree=utiliser_quantite_livree)
    numero_facture = operation.numero_facture or f"PROFORMA-{operation.numero_bl}"
    date_facture = operation.date_facture or timezone.localdate()
    type_quantite_label = "Quantite livree exacte" if utiliser_quantite_livree else "Quantite commandee"
    logo_path = r"C:\Users\HP\Downloads\Design-sans-titre-7.png"
    footer_text = (
        "Societe au capital de GNF 50 000 000 sise au quartier Koulewondy, commune de Kaloum - Conakry - Republique de Guinee\n"
        "BP : 5420P / TEL : 00224 620 59 75 34 / 00224 661 15 15 15 Email: patbeavoguigmail.com / N RCCM : GN.TCC.2020.B.103"
    )

    def fmt(value, decimals=2):
        text = f"{Decimal(value):,.{decimals}f}"
        return text.replace(",", " ").replace(".", ",")

    def wrap_text(value, max_width, font_name="Helvetica-Bold", font_size=8):
        words = (value or "").split()
        if not words:
            return [""]
        lines = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if pdf.stringWidth(candidate, font_name, font_size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def draw_box(x, y, w, h, radius=3 * mm):
        pdf.roundRect(x, y, w, h, radius, stroke=1, fill=0)

    def draw_label_value(x, y, label, value, font_size=8.5):
        pdf.setFont("Helvetica-Bold", font_size)
        pdf.drawString(x, y, label)
        pdf.setFont("Helvetica", font_size)
        pdf.drawString(x + 20 * mm, y, value or "-")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    if os.path.exists(logo_path):
        pdf.drawImage(ImageReader(logo_path), 12 * mm, height - 34 * mm, width=28 * mm, height=20 * mm, mask="auto")

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(52 * mm, height - 24 * mm, "FACTURE N°")
    pdf.drawString(112 * mm, height - 24 * mm, numero_facture)
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.drawString(88 * mm, height - 31 * mm, "Conakry le :")
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.drawString(120 * mm, height - 31 * mm, date_facture.strftime("%A %d %B %Y"))
    pdf.setFont("Helvetica", 7.5)
    pdf.drawRightString(width - 16 * mm, height - 38 * mm, type_quantite_label)

    left_box_y = height - 96 * mm
    left_box_h = 47 * mm
    draw_box(8 * mm, left_box_y, 94 * mm, left_box_h)
    draw_box(110 * mm, left_box_y, 92 * mm, left_box_h)

    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(12 * mm, left_box_y + left_box_h - 6 * mm, "Adresse Emetteur :")
    pdf.setFont("Helvetica", 7.5)
    emitter_lines = [
        "Commune de KALOUM - Q / KOULEWONDI",
        "BP :        5020P - CONAKRY - REP. DE GUIN",
        "RCCM :   GN.TTC.2020.B.10316",
        "NIF :       173604760, TVA : 7M",
        "TEL :       +224620597534",
        "Email :    contact@sonienergy.com",
    ]
    y = left_box_y + left_box_h - 13 * mm
    for line in emitter_lines:
        pdf.drawString(12 * mm, y, line)
        y -= 5.8 * mm

    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(114 * mm, left_box_y + left_box_h - 6 * mm, "Adresse facturation :")
    pdf.drawString(170 * mm, left_box_y + left_box_h - 6 * mm, "Code Client :")
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(190 * mm, left_box_y + left_box_h - 6 * mm, f"CLT{operation.client_id:03d}")
    pdf.drawString(114 * mm, left_box_y + left_box_h - 14 * mm, "Nom :")
    pdf.drawString(126 * mm, left_box_y + left_box_h - 14 * mm, operation.client.entreprise)
    pdf.drawString(114 * mm, left_box_y + left_box_h - 24 * mm, "Adresse :")
    pdf.drawString(130 * mm, left_box_y + left_box_h - 24 * mm, operation.client.adresse or "-")

    ref_y = height - 124 * mm
    draw_box(8 * mm, ref_y, 194 * mm, 15 * mm, radius=2 * mm)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(12 * mm, ref_y + 5.7 * mm, "Ref :")
    pdf.drawString(66 * mm, ref_y + 5.7 * mm, f"N° BL : {operation.numero_bl}")
    pdf.drawString(116 * mm, ref_y + 5.7 * mm, f"Lieu de livraison : {operation.destination}")

    pdf.drawString(10 * mm, ref_y - 12 * mm, "Devise :  GNF")
    pdf.drawRightString(200 * mm, ref_y - 12 * mm, "Page       1")

    table_top = height - 156 * mm
    header_h = 10 * mm
    body_h = 42 * mm
    total_qty_h = 8 * mm
    table_bottom = table_top - header_h - body_h - total_qty_h
    col_x = [10 * mm, 30 * mm, 60 * mm, 136 * mm, 160 * mm, 180 * mm, 200 * mm]
    header_y = table_top - header_h
    body_bottom_y = header_y - body_h

    pdf.setFillColor(colors.HexColor("#d9d9d9"))
    pdf.rect(10 * mm, header_y, 190 * mm, header_h, stroke=0, fill=1)
    pdf.setFillColor(colors.black)

    pdf.rect(10 * mm, table_bottom, 190 * mm, header_h + body_h + total_qty_h, stroke=1, fill=0)
    for x in col_x[1:-1]:
        pdf.line(x, body_bottom_y, x, table_top)
    pdf.line(10 * mm, header_y, 200 * mm, header_y)
    pdf.line(10 * mm, body_bottom_y, 200 * mm, body_bottom_y)

    pdf.setFont("Helvetica-Bold", 7.5)
    headers = ["Date CDE", "Réf CDE", "Désignation", "Qté fact.", "Prix Unitaire", "Montant"]
    header_centers = [
        (col_x[0] + col_x[1]) / 2,
        (col_x[1] + col_x[2]) / 2,
        (col_x[2] + col_x[3]) / 2,
        (col_x[3] + col_x[4]) / 2,
        (col_x[4] + col_x[5]) / 2,
        (col_x[5] + col_x[6]) / 2,
    ]
    for center, header in zip(header_centers, headers):
        pdf.drawCentredString(center, header_y + 3.3 * mm, header)

    pdf.setFont("Helvetica", 8)
    body_text_y = header_y - 6.5 * mm
    pdf.drawString(12 * mm, body_text_y, operation.commande.date_commande.strftime("%d/%m/%Y") if operation.commande else "-")
    pdf.drawString(33 * mm, body_text_y, operation.commande.reference if operation.commande else "-")
    pdf.drawString(61 * mm, body_text_y, operation.produit.nom if operation.produit else "-")
    pdf.drawRightString(156 * mm, body_text_y, fmt(totals["quantite"], 0))
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(157.5 * mm, body_text_y, "L")
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(178 * mm, body_text_y, fmt(totals["prix_unitaire"], 0))
    pdf.drawRightString(198 * mm, body_text_y, fmt(totals["montant_ht"], 0))

    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(136 * mm, table_bottom + 2.5 * mm, "Total quantité :")
    pdf.drawRightString(158 * mm, table_bottom + 2.5 * mm, fmt(totals["quantite"], 0))

    totals_x = 136 * mm
    totals_top = table_bottom
    box_w = 64 * mm
    row_h = 8 * mm
    label_split_x = totals_x + 24 * mm
    for i in range(3):
        row_y = totals_top - (i + 1) * row_h
        pdf.rect(totals_x, row_y, box_w, row_h, stroke=1, fill=0)
        pdf.line(label_split_x, row_y, label_split_x, row_y + row_h)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(totals_x + 2 * mm, totals_top - 5.4 * mm, "Montant HT")
    pdf.drawString(totals_x + 2 * mm, totals_top - row_h - 5.4 * mm, "Montant TVA")
    pdf.drawString(totals_x + 2 * mm, totals_top - 2 * row_h - 5.4 * mm, "Montant TTC")
    pdf.drawRightString(totals_x + box_w - 2 * mm, totals_top - 5.4 * mm, fmt(totals["montant_ht"], 0))
    pdf.drawRightString(totals_x + box_w - 2 * mm, totals_top - row_h - 5.4 * mm, fmt(totals["montant_tva"], 0))
    pdf.drawRightString(totals_x + box_w - 2 * mm, totals_top - 2 * row_h - 5.4 * mm, fmt(totals["montant_ttc"], 0))

    body_y = table_top - 84 * mm
    pdf.setFont("Helvetica", 8)
    pdf.drawString(10 * mm, body_y, "Sauf erreur ou omission de notre part, nous arretons la presente facture au montant :")
    montant_lettres = _amount_to_french_words(totals["montant_ttc" if avec_tva else "montant_ht"])
    pdf.setFont("Helvetica-Bold", 8)
    montant_lettres_lines = wrap_text(montant_lettres.upper(), 160 * mm, "Helvetica-Bold", 8)
    current_y = body_y - 6 * mm
    for line in montant_lettres_lines[:3]:
        pdf.drawString(10 * mm, current_y, line)
        current_y -= 4.5 * mm

    pdf.setFont("Helvetica", 8)
    pdf.drawString(10 * mm, body_y - 18 * mm, "Merci de bien vouloir regler cette facture au plus tard le :")
    pdf.drawString(102 * mm, body_y - 18 * mm, date_facture.strftime("%d/%m/%Y"))
    pdf.drawString(10 * mm, body_y - 28 * mm, "Mode de paiement :")
    pdf.drawString(42 * mm, body_y - 28 * mm, "par cheque de banque a la livraison")

    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(10 * mm, body_y - 44 * mm, "En votre aimable reglement")
    pdf.drawString(10 * mm, body_y - 50 * mm, "Cordialement")
    pdf.setFont("Helvetica", 7.5)
    pdf.drawRightString(190 * mm, body_y - 18 * mm, "LA COMPTABILITE")
    pdf.drawRightString(190 * mm, body_y - 48 * mm, "MARIAMA DJELO SOW")

    pdf.setFillColor(colors.HexColor("#c12f2f"))
    pdf.rect(10 * mm, 8 * mm, 190 * mm, 8 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 6.5)
    footer_lines = footer_text.splitlines()
    pdf.drawCentredString(width / 2, 13 * mm, footer_lines[0])
    pdf.drawCentredString(width / 2, 9 * mm, footer_lines[1])

    pdf.showPage()
    pdf.save()
    response = HttpResponse(content_type="application/pdf")
    suffix = "avec_tva" if avec_tva else "sans_tva"
    response["Content-Disposition"] = f'inline; filename="facture_{suffix}_{numero_facture}.pdf"'
    response.write(buffer.getvalue())
    return response


def _operations_queryset(request):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()

    operations = Operation.objects.select_related(
        "client",
        "camion",
        "chauffeur",
        "produit",
        "commande",
    )
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
            | Q(commande__reference__icontains=query)
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_debut:
        operations = operations.filter(date_bl__gte=date_debut)
    if date_fin:
        operations = operations.filter(date_bl__lte=date_fin)

    return operations, query, etat, date_debut, date_fin


def _secretaire_queryset(request):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    operations = Operation.objects.select_related("commande", "client", "camion", "chauffeur", "produit")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_from:
        operations = operations.filter(date_creation__date__gte=date_from)
    if date_to:
        operations = operations.filter(date_creation__date__lte=date_to)
    return operations, query, etat, date_from, date_to


def _transitaire_queryset(request):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    operations = Operation.objects.select_related("client", "camion", "chauffeur", "commande")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_from:
        operations = operations.filter(date_creation__date__gte=date_from)
    if date_to:
        operations = operations.filter(date_creation__date__lte=date_to)
    return operations, query, etat, date_from, date_to


def _transitaire_history_queryset(request):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    operations = Operation.objects.select_related("client", "camion", "chauffeur", "commande")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )
    operations = operations.filter(etat_bon__in=ETATS_TRANSITAIRE_HISTORIQUE)
    if etat:
        operations = operations.filter(etat_bon=etat)
        if etat == "livre":
            operations = operations.filter(date_bon_retour__isnull=True)
    if date_from:
        operations = operations.filter(date_reception_logistique__gte=date_from)
    if date_to:
        operations = operations.filter(date_reception_logistique__lte=date_to)
    return operations, query, etat, date_from, date_to


def _build_operations_excel_response(filename, rows, headers, sheet_title):
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
    sheet.title = sheet_title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


def _build_operations_pdf_response(filename, rows, headers, title="Rapport"):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        return HttpResponse(
            "Le module reportlab n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    styles = getSampleStyleSheet()
    data = [headers] + rows
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123047")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2e8")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f8fb")]),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("PADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    doc.build([Paragraph(title, styles["Heading2"]), Spacer(1, 8), table])

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write(buffer.getvalue())
    return response


def liste_operations(request):
    operations, query, etat, date_debut, date_fin = _operations_queryset(request)

    return render(
        request,
        "operations/operations.html",
        {
            "operations": operations,
            "query": query,
            "etat": etat,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "active_tab": "general",
            "etat_options": Operation.ETAT_BON_CHOICES,
            "current_filters": request.GET.urlencode(),
        },
    )


def export_operations_xls(request):
    try:
        from openpyxl import Workbook
    except ImportError:
        return HttpResponse(
            "Le module openpyxl n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    operations, _, _, _, _ = _operations_queryset(request)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Operations"
    sheet.append(
        [
            "BL",
            "Etat",
            "Commande",
            "Client",
            "Destination",
            "Camion",
            "Chauffeur",
            "Date BL",
            "Voyage",
            "Retour bon",
        ]
    )

    for operation in operations.order_by("-date_creation"):
        sheet.append(
            [
                operation.numero_bl,
                operation.get_etat_bon_display(),
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                operation.destination,
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                operation.date_bl.strftime("%Y-%m-%d") if operation.date_bl else "",
                operation.jours_voyage if operation.jours_voyage is not None else "",
                operation.jours_retour_bon if operation.jours_retour_bon is not None else "",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_operations.xlsx"'
    workbook.save(response)
    return response


def export_operations_pdf(request):
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

    operations, _, _, _, _ = _operations_queryset(request)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))

    data = [[
        "BL",
        "Etat",
        "Commande",
        "Client",
        "Destination",
        "Camion",
        "Chauffeur",
        "Date BL",
        "Voyage",
        "Retour bon",
    ]]

    for operation in operations.order_by("-date_creation"):
        data.append(
            [
                operation.numero_bl,
                operation.get_etat_bon_display(),
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                operation.destination,
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                operation.date_bl.strftime("%Y-%m-%d") if operation.date_bl else "",
                str(operation.jours_voyage if operation.jours_voyage is not None else ""),
                str(operation.jours_retour_bon if operation.jours_retour_bon is not None else ""),
            ]
        )

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
    response["Content-Disposition"] = 'attachment; filename="rapport_operations.pdf"'
    response.write(buffer.getvalue())
    return response


def ajouter_operation(request):
    if request.method == "POST":
        form = OperationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("operations")
    else:
        form = OperationForm()

    return render(
        request,
        "operations/ajouter_operation.html",
        {
            "form": form,
            "produit_form": ProduitForm(),
            "jours_voyage": None,
            "jours_retour_bon": None,
            "operation": None,
            "active_tab": "general",
        },
    )


def modifier_operation(request, id):
    operation = get_object_or_404(Operation, id=id)
    if request.method == "POST":
        form = OperationForm(request.POST, instance=operation)
        if form.is_valid():
            form.save()
            return redirect("operations")
    else:
        form = OperationForm(instance=operation)

    return render(
        request,
        "operations/modifier_operation.html",
        {
            "form": form,
            "produit_form": ProduitForm(),
            "operation": operation,
            "jours_voyage": operation.jours_voyage,
            "jours_retour_bon": operation.jours_retour_bon,
            "active_tab": "general",
        },
    )


def supprimer_operation(request, id):
    operation = get_object_or_404(Operation, id=id)
    operation.delete()
    return redirect("operations")


def comptable_operations(request):
    query = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "").strip()

    commandes_pretes_queryset = Commande.objects.select_related("client", "produit", "camion", "chauffeur").filter(
        statut="planifiee"
    )
    operations = Operation.objects.select_related("commande", "client", "produit", "camion", "chauffeur", "remplace_par").prefetch_related("anciennes_versions")

    if query:
        commandes_pretes_queryset = commandes_pretes_queryset.filter(
            Q(reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
        )

    commandes_pretes = []
    for commande in commandes_pretes_queryset.order_by("-date_creation"):
        quantite_commandee = Decimal(commande.quantite or 0)
        quantite_couverte = (
            commande.operations.filter(remplace_par__isnull=True).aggregate(total=Sum("quantite")).get("total")
            or Decimal("0.00")
        )
        quantite_restante = quantite_commandee - Decimal(quantite_couverte or 0)
        if quantite_restante > 0:
            commande.quantite_restante = quantite_restante
            commandes_pretes.append(commande)

    operations = list(operations.order_by("-date_creation"))
    for operation in operations:
        operation.comptable_can_edit = _comptable_operation_can_edit(operation)

    return render(
        request,
        "operations/comptable.html",
        {
            "commandes_pretes": commandes_pretes,
            "operations": operations,
            "query": query,
            "scope": scope,
            "active_tab": "comptable",
        },
    )


def secretaire_operations(request):
    operations, query, etat, date_from, date_to = _secretaire_queryset(request)

    operations_to_transmit = list(operations.filter(etat_bon="initie"))
    operations_transmitted = list(
        operations.exclude(etat_bon="initie").order_by("-date_transmission_depot", "-date_creation")
    )
    total_to_transmit = len(operations_to_transmit)
    total_transmitted = len(operations_transmitted)
    total_today = sum(
        1
        for operation in operations_transmitted
        if operation.date_transmission_depot and operation.date_transmission_depot == timezone.localdate()
    )

    return render(
        request,
        "operations/secretaire.html",
        {
            "operations_to_transmit": operations_to_transmit,
            "operations_transmitted": operations_transmitted,
            "query": query,
            "etat": etat,
            "date_from": date_from,
            "date_to": date_to,
            "total_to_transmit": total_to_transmit,
            "total_transmitted": total_transmitted,
            "total_today": total_today,
            "active_tab": "secretaire",
            "current_filters": request.GET.urlencode(),
        },
    )


def export_secretaire_xls(request):
    operations, _, _, _, _ = _secretaire_queryset(request)
    rows = []
    for operation in operations.order_by("-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                operation.get_etat_bon_display(),
                operation.date_creation.strftime("%Y-%m-%d") if operation.date_creation else "",
                operation.date_transmission_depot.strftime("%Y-%m-%d") if operation.date_transmission_depot else "",
                operation.date_reception_transitaire.strftime("%Y-%m-%d") if operation.date_reception_transitaire else "",
            ]
        )
    return _build_operations_excel_response(
        "rapport_secretaire.xlsx",
        rows,
        ["BL", "Commande", "Client", "Camion", "Chauffeur", "Etat", "Creation", "Transmission depot", "Reception transitaire"],
        "Secretaire BL",
    )


def export_secretaire_pdf(request):
    operations, _, _, _, _ = _secretaire_queryset(request)
    rows = []
    for operation in operations.order_by("-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                operation.get_etat_bon_display(),
                operation.camion.numero_tracteur if operation.camion else "",
                operation.date_transmission_depot.strftime("%d/%m/%Y") if operation.date_transmission_depot else "-",
                operation.date_reception_transitaire.strftime("%d/%m/%Y") if operation.date_reception_transitaire else "-",
            ]
        )
    return _build_operations_pdf_response(
        "rapport_secretaire.pdf",
        rows,
        ["BL", "Commande", "Client", "Etat", "Camion", "Transmission", "Reception"],
        "Rapport secretaire BL",
    )


def transmettre_bons_secretaire(request):
    if request.method != "POST":
        return redirect("secretaire_operations")

    date_raw = (request.POST.get("date_action") or "").strip()
    selected_ids = request.POST.getlist("selected_operations")

    if not selected_ids:
        messages.error(request, "Selectionnez au moins un BL a transmettre au depot.")
        return redirect("secretaire_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date de transmission au depot.")
        return redirect("secretaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("secretaire_operations")

    updated_count = 0
    for operation in Operation.objects.filter(id__in=selected_ids):
        if operation.etat_bon != "initie":
            continue
        operation.date_transmission_depot = action_date
        operation.etat_bon = "attente_reception_transitaire"
        try:
            operation.full_clean()
            operation.save()
            updated_count += 1
        except ValidationError:
            continue

    if updated_count:
        messages.success(request, f"{updated_count} BL transmis au depot et envoyes au transitaire.")
    else:
        messages.error(request, "Aucun BL n'a pu etre transmis.")

    return redirect("secretaire_operations")


def sommiers_operations(request):
    query = request.GET.get("q", "").strip()
    produit = request.GET.get("produit", "").strip()
    stats_date = request.GET.get("stats_date", "").strip() or str(timezone.localdate())

    sommiers = Sommier.objects.select_related("produit")
    if query:
        sommiers = sommiers.filter(
            Q(numero_sm__icontains=query)
            | Q(reference_navire__icontains=query)
            | Q(produit__nom__icontains=query)
        )
    if produit:
        sommiers = sommiers.filter(produit_id=produit)

    totals_by_product = (
        Sommier.objects.select_related("produit")
        .values("produit__nom")
        .annotate(total_stock=Sum("quantite_disponible"))
        .order_by("produit__nom")
    )
    totals_by_product = list(totals_by_product)
    max_total_stock = max((item["total_stock"] or 0 for item in totals_by_product), default=0)
    chart_totals = []
    for index, item in enumerate(totals_by_product):
        total_stock = item["total_stock"] or 0
        width_percent = 0
        if max_total_stock:
            width_percent = max(10, int((total_stock / max_total_stock) * 100))
        chart_totals.append(
            {
                "label": item["produit__nom"],
                "total_stock": total_stock,
                "width_percent": width_percent,
                "color": "#d9534f" if "ess" in (item["produit__nom"] or "").lower() else "#1f8f6a",
            }
        )

    sorties_queryset = (
        Operation.objects.filter(etat_bon="liquide", date_bons_liquides=stats_date)
        .values("produit__nom")
        .annotate(total_sortie=Sum("quantite"))
        .order_by("produit__nom")
    )
    sorties_queryset = list(sorties_queryset)
    max_sortie = max((item["total_sortie"] or 0 for item in sorties_queryset), default=0)
    chart_sorties = []
    for item in sorties_queryset:
        total_sortie = item["total_sortie"] or 0
        width_percent = 0
        if max_sortie:
            width_percent = max(10, int((total_sortie / max_sortie) * 100))
        chart_sorties.append(
            {
                "label": item["produit__nom"],
                "total_sortie": total_sortie,
                "width_percent": width_percent,
                "color": "#d9534f" if "ess" in (item["produit__nom"] or "").lower() else "#1f8f6a",
            }
        )

    if request.method == "POST":
        form = SommierForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Le sommier a bien ete enregistre.")
            return redirect("sommiers_operations")
    else:
        form = SommierForm(initial={"date_sommier": timezone.localdate()})

    return render(
        request,
        "operations/sommiers.html",
        {
            "sommiers": sommiers,
            "form": form,
            "query": query,
            "produit_filter": produit,
            "totals_by_product": totals_by_product,
            "chart_totals": chart_totals,
            "stats_date": stats_date,
            "chart_sorties": chart_sorties,
            "produits": Produit.objects.order_by("nom"),
            "active_tab": "sommiers",
        },
    )


def ajouter_operation_comptable(request):
    commande_id = request.GET.get("commande_id")
    commande_initiale = None
    allocation_rows = []
    quantite_restante_commande = None
    if commande_id:
        commande_initiale = get_object_or_404(
            Commande.objects.select_related("client", "produit", "camion", "chauffeur"),
            id=commande_id,
        )
        quantite_couverte = (
            commande_initiale.operations.filter(remplace_par__isnull=True).aggregate(total=Sum("quantite")).get("total")
            or Decimal("0.00")
        )
        quantite_restante_commande = Decimal(commande_initiale.quantite or 0) - Decimal(quantite_couverte or 0)
        if quantite_restante_commande <= 0:
            messages.info(request, "Cette commande est deja completement repartie en BL.")
            return redirect("comptable_operations")

    if request.method == "POST":
        form = ComptableOperationForm(request.POST, allow_multiple_allocations=True)
        if form.is_valid():
            commande = form.cleaned_data.get("commande")
            if not commande:
                form.add_error("commande", "Selectionnez une commande avant de repartir les BL.")
            else:
                allocation_bl_numbers = request.POST.getlist("allocation_numero_bl")
                allocation_sommier_ids = request.POST.getlist("allocation_sommier")
                allocation_quantites = request.POST.getlist("allocation_quantite")
                quantite_existante = (
                    commande.operations.filter(remplace_par__isnull=True).aggregate(total=Sum("quantite")).get("total")
                    or Decimal("0.00")
                )
                quantite_restante = Decimal(commande.quantite or 0) - Decimal(quantite_existante or 0)

                raw_allocations = [
                    {
                        "numero_bl": (numero_bl or "").strip(),
                        "sommier": (sommier_id or "").strip(),
                        "quantite": (quantite or "").strip(),
                    }
                    for numero_bl, sommier_id, quantite in zip(allocation_bl_numbers, allocation_sommier_ids, allocation_quantites)
                ]
                lignes_renseignees = [
                    row for row in raw_allocations
                    if row["numero_bl"] or row["sommier"] or row["quantite"]
                ]

                if len(lignes_renseignees) == 1:
                    row = lignes_renseignees[0]
                    if row["numero_bl"] and row["sommier"] and not row["quantite"] and quantite_restante > 0:
                        sommier_stock = (
                            Sommier.objects.filter(id=row["sommier"]).values_list("quantite_disponible", flat=True).first()
                            or Decimal("0.00")
                        )
                        row["quantite"] = str(min(quantite_restante, Decimal(sommier_stock or 0)))

                allocation_rows = [
                    {
                        "numero_bl": row["numero_bl"],
                        "sommier": row["sommier"],
                        "quantite": row["quantite"],
                    }
                    for row in raw_allocations
                    if row["numero_bl"] or row["sommier"] or row["quantite"]
                ]

                allocations = []
                for index, row in enumerate(raw_allocations, start=1):
                    numero_bl = row["numero_bl"]
                    sommier_id = row["sommier"]
                    quantite_raw = row["quantite"]
                    if not numero_bl and not sommier_id and not quantite_raw:
                        continue
                    if not numero_bl or not sommier_id or not quantite_raw:
                        form.add_error(None, f"Ligne {index}: completez le numero BL, le sommier et la quantite.")
                        continue
                    try:
                        quantite_value = _parse_decimal_input(quantite_raw)
                    except Exception:
                        form.add_error(None, f"Ligne {index}: la quantite saisie est invalide.")
                        continue
                    if quantite_value <= 0:
                        form.add_error(None, f"Ligne {index}: la quantite doit etre superieure a zero.")
                        continue
                    allocations.append(
                        {
                            "numero_bl": numero_bl,
                            "sommier_id": int(sommier_id),
                            "quantite": quantite_value,
                            "index": index,
                        }
                    )

                if not allocations:
                    form.add_error(None, "Ajoutez au moins une ligne d'allocation pour creer les BL.")
                else:
                    total_allocations = sum((item["quantite"] for item in allocations), Decimal("0.00"))
                    if total_allocations != quantite_restante:
                        form.add_error(
                            None,
                            f"Le total alloue ({total_allocations}) doit etre exactement egal a la quantite restante de la commande ({quantite_restante}).",
                        )

                    duplicate_numbers = set()
                    seen_numbers = set()
                    for item in allocations:
                        if item["numero_bl"] in seen_numbers:
                            duplicate_numbers.add(item["numero_bl"])
                        seen_numbers.add(item["numero_bl"])
                    if duplicate_numbers:
                        form.add_error(None, f"Les numeros BL suivants sont dupliques dans la saisie: {', '.join(sorted(duplicate_numbers))}.")

                    existing_numbers = set(
                        Operation.objects.filter(numero_bl__in=[item["numero_bl"] for item in allocations]).values_list("numero_bl", flat=True)
                    )
                    if existing_numbers:
                        form.add_error(None, f"Les numeros BL suivants existent deja: {', '.join(sorted(existing_numbers))}.")

                if not form.errors:
                    with transaction.atomic():
                        sommier_ids = sorted({item["sommier_id"] for item in allocations})
                        sommiers = {
                            sommier.id: sommier
                            for sommier in Sommier.objects.select_for_update().select_related("produit").filter(id__in=sommier_ids)
                        }
                        sommier_usage = {}
                        for item in allocations:
                            sommier = sommiers.get(item["sommier_id"])
                            if not sommier:
                                form.add_error(None, f"Ligne {item['index']}: sommier introuvable.")
                                continue
                            if commande.produit_id and sommier.produit_id != commande.produit_id:
                                form.add_error(None, f"Ligne {item['index']}: le sommier {sommier.numero_sm} ne correspond pas au produit de la commande.")
                                continue
                            sommier_usage.setdefault(sommier.id, Decimal("0.00"))
                            sommier_usage[sommier.id] += item["quantite"]

                        for sommier_id, quantite_utilisee in sommier_usage.items():
                            sommier = sommiers[sommier_id]
                            disponible = Decimal(sommier.quantite_disponible or 0)
                            if quantite_utilisee > disponible:
                                form.add_error(
                                    None,
                                    f"Le sommier {sommier.numero_sm} n'a que {disponible} disponible alors que {quantite_utilisee} est demande.",
                                )

                        if not form.errors:
                            common_values = {
                                "commande": commande,
                                "client": commande.client,
                                "produit": commande.produit,
                                "destination": commande.ville_arrivee,
                                "camion": commande.camion,
                                "chauffeur": commande.chauffeur,
                                "regime_douanier": form.cleaned_data.get("regime_douanier"),
                                "depot": form.cleaned_data.get("depot"),
                                "date_bl": form.cleaned_data.get("date_bl"),
                                "observation": form.cleaned_data.get("observation") or "",
                                "etat_bon": "initie",
                            }
                            created_ids = []
                            for item in allocations:
                                sommier = sommiers[item["sommier_id"]]
                                operation = Operation.objects.create(
                                    numero_bl=item["numero_bl"],
                                    sommier=sommier,
                                    quantite=item["quantite"],
                                    stock_sommier_deduit=True,
                                    **common_values,
                                )
                                created_ids.append(operation.id)
                                sommier.quantite_disponible = Decimal(sommier.quantite_disponible or 0) - item["quantite"]
                                sommier.save(update_fields=["quantite_disponible"])
                            messages.success(request, f"{len(created_ids)} BL ont ete crees pour la commande {commande.reference}.")
                            return redirect("comptable_operations")
    else:
        initial = {}
        if commande_initiale:
            initial = {
                "commande": commande_initiale.id,
                "client": commande_initiale.client_id,
                "destination": commande_initiale.ville_arrivee,
                "produit": commande_initiale.produit_id,
                "quantite": quantite_restante_commande if quantite_restante_commande is not None else commande_initiale.quantite,
            }
        form = ComptableOperationForm(initial=initial, allow_multiple_allocations=True)
        allocation_rows = [{}]

    return render(
        request,
        "operations/comptable_form.html",
        {
            "form": form,
            "regime_form": RegimeDouanierForm(),
            "depot_form": DepotForm(),
            "produit_form": ProduitForm(),
            "sommier_form": SommierForm(),
            "page_title": "Nouvelle fiche comptable",
            "submit_label": "Creer les BL",
            "active_tab": "comptable",
            "commande_initiale": commande_initiale,
            "quantite_restante_commande": quantite_restante_commande,
            "allocation_rows": allocation_rows,
        },
    )


def modifier_operation_comptable(request, id):
    operation = get_object_or_404(Operation, id=id)
    if not _comptable_operation_can_edit(operation):
        messages.error(
            request,
            "Ce BL ne peut plus etre modifie par la comptabilite car le transitaire a deja valide sa reception.",
        )
        return redirect("/operations/comptable/?scope=historique")

    editable_operations = []
    multi_allocation_mode = False
    allocation_rows = []
    allocation_target_total = None
    commande_initiale = operation.commande

    if operation.commande_id:
        editable_operations = list(_get_comptable_editable_allocations(operation.commande))
        if editable_operations and all(_comptable_operation_can_edit(item) for item in editable_operations):
            multi_allocation_mode = True
            allocation_target_total = sum((Decimal(item.quantite or 0) for item in editable_operations), Decimal("0.00"))
            allocation_rows = [
                {
                    "numero_bl": item.numero_bl,
                    "sommier": str(item.sommier_id or ""),
                    "quantite": str(item.quantite or ""),
                }
                for item in editable_operations
            ] or [{}]

    if multi_allocation_mode and request.method == "POST":
        form = ComptableOperationForm(request.POST, instance=operation, allow_multiple_allocations=True)
        if form.is_valid():
            if form.cleaned_data.get("commande") and form.cleaned_data.get("commande").id != operation.commande_id:
                form.add_error("commande", "La commande d'origine du BL ne peut pas etre changee dans ce mode de repartition.")

            allocation_bl_numbers = request.POST.getlist("allocation_numero_bl")
            allocation_sommier_ids = request.POST.getlist("allocation_sommier")
            allocation_quantites = request.POST.getlist("allocation_quantite")

            raw_allocations = [
                {
                    "numero_bl": (numero_bl or "").strip(),
                    "sommier": (sommier_id or "").strip(),
                    "quantite": (quantite or "").strip(),
                }
                for numero_bl, sommier_id, quantite in zip(allocation_bl_numbers, allocation_sommier_ids, allocation_quantites)
            ]
            allocation_rows = [
                {
                    "numero_bl": row["numero_bl"],
                    "sommier": row["sommier"],
                    "quantite": row["quantite"],
                }
                for row in raw_allocations
                if row["numero_bl"] or row["sommier"] or row["quantite"]
            ] or [{}]

            allocations = []
            for index, row in enumerate(raw_allocations, start=1):
                numero_bl = row["numero_bl"]
                sommier_id = row["sommier"]
                quantite_raw = row["quantite"]
                if not numero_bl and not sommier_id and not quantite_raw:
                    continue
                if not numero_bl or not sommier_id or not quantite_raw:
                    form.add_error(None, f"Ligne {index}: completez le numero BL, le sommier et la quantite.")
                    continue
                try:
                    quantite_value = _parse_decimal_input(quantite_raw)
                except Exception:
                    form.add_error(None, f"Ligne {index}: la quantite saisie est invalide.")
                    continue
                if quantite_value <= 0:
                    form.add_error(None, f"Ligne {index}: la quantite doit etre superieure a zero.")
                    continue
                allocations.append(
                    {
                        "numero_bl": numero_bl,
                        "sommier_id": int(sommier_id),
                        "quantite": quantite_value,
                        "index": index,
                    }
                )

            if not allocations:
                form.add_error(None, "Ajoutez au moins une ligne d'allocation pour mettre a jour les BL.")
            else:
                total_allocations = sum((item["quantite"] for item in allocations), Decimal("0.00"))
                if total_allocations != allocation_target_total:
                    form.add_error(
                        None,
                        f"Le total alloue ({total_allocations}) doit rester egal a la quantite de la repartition ({allocation_target_total}).",
                    )

                duplicate_numbers = set()
                seen_numbers = set()
                for item in allocations:
                    if item["numero_bl"] in seen_numbers:
                        duplicate_numbers.add(item["numero_bl"])
                    seen_numbers.add(item["numero_bl"])
                if duplicate_numbers:
                    form.add_error(None, f"Les numeros BL suivants sont dupliques dans la saisie: {', '.join(sorted(duplicate_numbers))}.")

                existing_ids = [item.id for item in editable_operations]
                existing_numbers = set(
                    Operation.objects.exclude(id__in=existing_ids)
                    .filter(numero_bl__in=[item["numero_bl"] for item in allocations])
                    .values_list("numero_bl", flat=True)
                )
                if existing_numbers:
                    form.add_error(None, f"Les numeros BL suivants existent deja: {', '.join(sorted(existing_numbers))}.")

            if not form.errors:
                with transaction.atomic():
                    base_operation = (
                        Operation.objects.select_for_update().select_related("commande", "client", "produit", "camion", "chauffeur")
                        .get(pk=operation.pk)
                    )
                    locked_editable_operations = list(
                        _get_comptable_editable_allocations(base_operation.commande).select_for_update()
                    )
                    locked_ids = [item.id for item in locked_editable_operations]
                    sommiers = {
                        sommier.id: sommier
                        for sommier in Sommier.objects.select_for_update().select_related("produit").filter(
                            id__in=sorted({item["sommier_id"] for item in allocations} | {item.sommier_id for item in locked_editable_operations if item.sommier_id})
                        )
                    }

                    previous_usage = {}
                    for existing_operation in locked_editable_operations:
                        if existing_operation.sommier_id and existing_operation.stock_sommier_deduit:
                            previous_usage.setdefault(existing_operation.sommier_id, Decimal("0.00"))
                            previous_usage[existing_operation.sommier_id] += Decimal(existing_operation.quantite or 0)

                    requested_usage = {}
                    for item in allocations:
                        sommier = sommiers.get(item["sommier_id"])
                        if not sommier:
                            form.add_error(None, f"Ligne {item['index']}: sommier introuvable.")
                            continue
                        if base_operation.commande.produit_id and sommier.produit_id != base_operation.commande.produit_id:
                            form.add_error(None, f"Ligne {item['index']}: le sommier {sommier.numero_sm} ne correspond pas au produit de la commande.")
                            continue
                        requested_usage.setdefault(sommier.id, Decimal("0.00"))
                        requested_usage[sommier.id] += item["quantite"]

                    for sommier_id, quantite_demandee in requested_usage.items():
                        sommier = sommiers[sommier_id]
                        disponible = Decimal(sommier.quantite_disponible or 0) + previous_usage.get(sommier_id, Decimal("0.00"))
                        if quantite_demandee > disponible:
                            form.add_error(
                                None,
                                f"Le sommier {sommier.numero_sm} n'a que {disponible} disponible pour cette repartition alors que {quantite_demandee} est demande.",
                            )

                    if not form.errors:
                        for existing_operation in locked_editable_operations:
                            if existing_operation.sommier_id and existing_operation.stock_sommier_deduit:
                                sommier = sommiers.get(existing_operation.sommier_id)
                                if sommier:
                                    sommier.quantite_disponible = Decimal(sommier.quantite_disponible or 0) + Decimal(existing_operation.quantite or 0)
                                    sommier.save(update_fields=["quantite_disponible"])

                        base_state = base_operation.etat_bon
                        transmission_date = base_operation.date_transmission_depot
                        for existing_operation in locked_editable_operations:
                            existing_operation.delete()

                        common_values = {
                            "commande": base_operation.commande,
                            "client": base_operation.commande.client,
                            "produit": base_operation.commande.produit,
                            "destination": base_operation.commande.ville_arrivee,
                            "camion": base_operation.commande.camion,
                            "chauffeur": base_operation.commande.chauffeur,
                            "regime_douanier": form.cleaned_data.get("regime_douanier"),
                            "depot": form.cleaned_data.get("depot"),
                            "date_bl": form.cleaned_data.get("date_bl"),
                            "observation": form.cleaned_data.get("observation") or "",
                            "etat_bon": base_state,
                            "date_transmission_depot": transmission_date,
                        }
                        created_count = 0
                        for item in allocations:
                            sommier = sommiers[item["sommier_id"]]
                            Operation.objects.create(
                                numero_bl=item["numero_bl"],
                                sommier=sommier,
                                quantite=item["quantite"],
                                stock_sommier_deduit=True,
                                **common_values,
                            )
                            sommier.quantite_disponible = Decimal(sommier.quantite_disponible or 0) - item["quantite"]
                            sommier.save(update_fields=["quantite_disponible"])
                            created_count += 1

                        messages.success(
                            request,
                            f"La repartition comptable de la commande {base_operation.commande.reference} a ete mise a jour sur {created_count} BL.",
                        )
                        return redirect("/operations/comptable/?scope=historique")
    elif multi_allocation_mode:
        form = ComptableOperationForm(instance=operation, allow_multiple_allocations=True)
    elif request.method == "POST":
        form = ComptableOperationForm(request.POST, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.commande_id:
                operation.client = operation.commande.client
                operation.produit = operation.commande.produit
                operation.destination = operation.commande.ville_arrivee
                operation.camion = operation.commande.camion
                operation.chauffeur = operation.commande.chauffeur
            operation.save()
            return redirect("comptable_operations")
    else:
        form = ComptableOperationForm(instance=operation)

    return render(
        request,
        "operations/comptable_form.html",
        {
            "form": form,
            "regime_form": RegimeDouanierForm(),
            "depot_form": DepotForm(),
            "produit_form": ProduitForm(),
            "sommier_form": SommierForm(),
            "page_title": f"Comptable BL {operation.numero_bl}",
            "submit_label": "Mettre a jour la repartition" if multi_allocation_mode else "Mettre a jour",
            "active_tab": "comptable",
            "commande_initiale": commande_initiale,
            "allocation_rows": allocation_rows,
            "allocation_target_total": allocation_target_total,
            "multi_allocation_mode": multi_allocation_mode,
        },
    )


def supprimer_operation_comptable(request, id):
    operation = get_object_or_404(Operation.objects.select_related("commande"), id=id)
    if request.method != "POST":
        return redirect("/operations/comptable/?scope=historique")

    if not _comptable_operation_can_edit(operation):
        messages.error(
            request,
            "Ce BL ne peut plus etre supprime par la comptabilite car le transitaire a deja valide sa reception.",
        )
        return redirect("/operations/comptable/?scope=historique")

    numero_bl = operation.numero_bl
    commande_reference = operation.commande.reference if operation.commande_id else ""
    with transaction.atomic():
        locked_operation = Operation.objects.select_for_update().select_related("commande").get(pk=operation.pk)
        if not _comptable_operation_can_edit(locked_operation):
            messages.error(
                request,
                "Ce BL ne peut plus etre supprime par la comptabilite car le transitaire a deja valide sa reception.",
            )
            return redirect("/operations/comptable/?scope=historique")
        _restore_operation_sommier_stock(locked_operation)
        locked_operation.delete()

    if commande_reference:
        messages.success(request, f"Le BL {numero_bl} a ete supprime de la commande {commande_reference}.")
    else:
        messages.success(request, f"Le BL {numero_bl} a ete supprime.")
    return redirect("/operations/comptable/?scope=historique")


def facturation_operations(request):
    query = request.GET.get("q", "").strip()
    statut = request.GET.get("statut_facture", "").strip()
    operations = Operation.objects.select_related("commande", "client", "produit", "remplace_par", "camion").filter(etat_bon="livre")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
        )
    if statut == "a_facturer":
        operations = operations.filter(Q(numero_facture="") | Q(numero_facture__isnull=True))
    elif statut == "facture":
        operations = operations.exclude(Q(numero_facture="") | Q(numero_facture__isnull=True))

    return render(
        request,
        "operations/facturation.html",
        {
            "operations": operations,
            "query": query,
            "statut_facture": statut,
            "active_tab": "facturation",
        },
    )


def modifier_operation_facturation(request, id):
    operation = get_object_or_404(Operation, id=id, etat_bon="livre")
    if operation.remplace_par_id:
        messages.error(request, "Ce BL n'est plus valide suite au changement de camion. Il reste visible mais n'est plus facturable.")
        return redirect("facturation_operations")
    if request.method == "POST":
        form = FacturationOperationForm(request.POST, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.numero_facture and not operation.date_facture:
                operation.date_facture = timezone.localdate()
            if operation.commande and operation.commande.prix_negocie is not None and operation.quantite is not None:
                totals = _facture_totals(operation, avec_tva=False)
                operation.montant_facture = totals["montant_ht"]
            operation.save()
            return redirect("facturation_operations")
    else:
        form = FacturationOperationForm(instance=operation)
        if not form.initial.get("date_facture"):
            form.initial["date_facture"] = timezone.localdate()
        if operation.commande and operation.commande.prix_negocie is not None and operation.quantite is not None and not form.initial.get("montant_facture"):
            form.initial["montant_facture"] = _facture_totals(operation, avec_tva=False)["montant_ht"]

    return render(
        request,
        "operations/facturation_form.html",
        {
            "form": form,
            "operation": operation,
            "active_tab": "facturation",
            "facture_totals_ht": _facture_totals(operation, avec_tva=False),
            "facture_totals_tva": _facture_totals(operation, avec_tva=True),
        },
    )


def imprimer_facture_sans_tva(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "commande", "produit", "camion", "chauffeur"),
        id=id,
        etat_bon="livre",
    )
    return _build_facture_pdf(operation, avec_tva=False, utiliser_quantite_livree=False)


def imprimer_facture_avec_tva(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "commande", "produit", "camion", "chauffeur"),
        id=id,
        etat_bon="livre",
    )
    return _build_facture_pdf(operation, avec_tva=True, utiliser_quantite_livree=False)


def imprimer_facture_sans_tva_manquant(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "commande", "produit", "camion", "chauffeur"),
        id=id,
        etat_bon="livre",
    )
    return _build_facture_pdf(operation, avec_tva=False, utiliser_quantite_livree=True)


def imprimer_facture_avec_tva_manquant(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "commande", "produit", "camion", "chauffeur"),
        id=id,
        etat_bon="livre",
    )
    return _build_facture_pdf(operation, avec_tva=True, utiliser_quantite_livree=True)


def logistique_operations(request):
    if get_user_role(request.user) == "logistique":
        target = "/commandes/"
        if request.GET.get("scope") == "historique":
            target = "/commandes/?scope=historique"
        query = request.GET.get("q", "").strip()
        if query:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}q={query}"
        return redirect(target)

    query = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "").strip()
    operations = (
        Operation.objects.select_related("client", "camion", "chauffeur", "commande")
        .prefetch_related("historiques_affectation")
        .annotate(
            has_reaffectation=Exists(
                HistoriqueAffectationOperation.objects.filter(operation_id=OuterRef("pk"))
            )
        )
    )
    if scope == "historique":
        operations = operations.filter(camion__isnull=False)
    else:
        operations = operations.filter(camion__isnull=True)
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )

    return render(
        request,
        "operations/logistique.html",
        {
            "operations": operations,
            "query": query,
            "scope": scope,
            "active_tab": "logistique",
        },
    )


def modifier_operation_logistique(request, id):
    operation = get_object_or_404(Operation, id=id)
    camion_capacites = {
        camion.id: {
            "capacite": camion.capacite,
            "numero": camion.numero_tracteur,
            "numero_citerne": camion.numero_citerne,
            "etat": camion.etat,
            "etat_label": camion.get_etat_display(),
        }
        for camion in Camion.objects.order_by("numero_tracteur")
    }
    if request.method == "POST":
        form = LogistiqueOperationForm(request.POST, instance=operation)
        if form.is_valid():
            ancienne_operation = Operation.objects.select_related("camion", "chauffeur").get(pk=operation.pk)
            operation_modifiee = form.save(commit=False)
            camion_change = ancienne_operation.camion_id != operation_modifiee.camion_id
            doit_tracer = camion_change and ancienne_operation.etat_bon in {"charge", "livre"}
            operation_modifiee.save()
            if doit_tracer:
                HistoriqueAffectationOperation.objects.create(
                    operation=operation_modifiee,
                    ancien_camion=ancienne_operation.camion,
                    ancien_chauffeur=ancienne_operation.chauffeur,
                    ancien_livreur=ancienne_operation.livreur,
                    ancienne_date_decharge_chauffeur=ancienne_operation.date_decharge_chauffeur,
                    ancienne_heure_decharge_chauffeur=ancienne_operation.heure_decharge_chauffeur,
                    ancien_etat_bon=ancienne_operation.etat_bon,
                    nouveau_camion=operation_modifiee.camion,
                    nouveau_chauffeur=operation_modifiee.chauffeur,
                )
                messages.warning(
                    request,
                    (
                        "Le camion du BL a ete modifie apres chargement. "
                        "L'ancienne affectation a ete conservee dans l'historique."
                    ),
                )
            return redirect("logistique_operations")
    else:
        form = LogistiqueOperationForm(instance=operation)

    return render(
        request,
        "operations/logistique_form.html",
        {
            "form": form,
            "operation": operation,
            "active_tab": "logistique",
            "camion_capacites": camion_capacites,
            "historiques_affectation": operation.historiques_affectation.select_related(
                "ancien_camion",
                "ancien_chauffeur",
                "nouveau_camion",
                "nouveau_chauffeur",
            ),
        },
    )


def ancienne_fiche_operation_logistique(request, id):
    historique = get_object_or_404(
        HistoriqueAffectationOperation.objects.select_related(
            "operation",
            "operation__client",
            "operation__commande",
            "ancien_camion",
            "ancien_camion__transporteur",
            "ancien_chauffeur",
            "nouveau_camion",
            "nouveau_chauffeur",
        ),
        id=id,
    )
    return render(
        request,
        "operations/logistique_old_sheet.html",
        {
            "historique": historique,
            "operation": historique.operation,
            "active_tab": "logistique",
        },
    )


def transitaire_operations(request):
    operations, query, etat, date_from, date_to = _transitaire_queryset(request)

    operations_pending_reception = list(
        operations.filter(etat_bon__in=ETATS_TRANSITAIRE_RECEPTION).order_by("date_transmission_depot", "numero_bl")
    )
    operations_in_progress = list(
        operations.filter(etat_bon__in=ETATS_TRANSITAIRE_TRAITEMENT).order_by("commande_id", "date_creation", "numero_bl")
    )
    _annotate_commande_groups(operations_pending_reception)
    _annotate_commande_groups(operations_in_progress)

    for operation in operations_pending_reception:
        operation.transitaire_dates_locked = _operation_is_locked_after_charge(operation)
        operation.can_validate_reception = (
            not operation.transitaire_dates_locked
            and operation.etat_bon == "attente_reception_transitaire"
            and bool(operation.date_transmission_depot)
            and not operation.date_reception_transitaire
        )

    for operation in operations_in_progress:
        operation.transitaire_dates_locked = _operation_is_locked_after_charge(operation)
        operation.can_declare = operation.etat_bon == "transmis" and not operation.transitaire_dates_locked
        operation.can_liquide = operation.etat_bon == "declare" and not operation.transitaire_dates_locked
        operation.can_transfer_logistique = operation.etat_bon == "liquide" and not operation.transitaire_dates_locked
        operation.can_charge_direct = operation.etat_bon == "liquide" and not operation.transitaire_dates_locked
        operation.status_label = _operation_status_label(operation)

    total_pending_reception = len(operations_pending_reception)
    total_to_declare = sum(1 for operation in operations_in_progress if operation.etat_bon == "transmis")
    total_to_liquide = sum(1 for operation in operations_in_progress if operation.etat_bon == "declare")
    total_to_orient = sum(1 for operation in operations_in_progress if operation.etat_bon == "liquide")
    total_essence = sum(
        operation.quantite or Decimal("0.00")
        for operation in list(operations_pending_reception) + list(operations_in_progress)
        if operation.produit_id and "ESSENCE" in (operation.produit.nom or "").upper()
    )
    total_gasoil = sum(
        operation.quantite or Decimal("0.00")
        for operation in list(operations_pending_reception) + list(operations_in_progress)
        if operation.produit_id and "GASOIL" in (operation.produit.nom or "").upper()
    )
    total_filtre = total_essence + total_gasoil

    return render(
        request,
        "operations/transitaire.html",
        {
            "operations_pending_reception": operations_pending_reception,
            "operations_in_progress": operations_in_progress,
            "query": query,
            "etat": etat,
            "date_from": date_from,
            "date_to": date_to,
            "total_pending_reception": total_pending_reception,
            "total_to_declare": total_to_declare,
            "total_to_liquide": total_to_liquide,
            "total_to_orient": total_to_orient,
            "total_essence_filtre": total_essence,
            "total_gasoil_filtre": total_gasoil,
            "total_filtre": total_filtre,
            "current_filters": request.GET.urlencode(),
            "active_tab": "transitaire",
        },
    )


def export_transitaire_xls(request):
    operations, _, _, _, _ = _transitaire_queryset(request)
    rows = []
    for operation in operations.order_by("-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                _operation_status_label(operation),
                operation.date_transmission_depot.strftime("%Y-%m-%d") if operation.date_transmission_depot else "",
                operation.date_reception_transitaire.strftime("%Y-%m-%d") if operation.date_reception_transitaire else "",
                operation.date_bons_declares.strftime("%Y-%m-%d") if operation.date_bons_declares else "",
                operation.date_bons_liquides.strftime("%Y-%m-%d") if operation.date_bons_liquides else "",
            ]
        )
    return _build_operations_excel_response(
        "rapport_transitaire.xlsx",
        rows,
        ["BL", "Commande", "Client", "Camion", "Chauffeur", "Etat", "Transmission", "Reception", "Declaration", "Liquidation"],
        "Transitaire",
    )


def export_transitaire_pdf(request):
    operations, _, _, _, _ = _transitaire_queryset(request)
    rows = []
    for operation in operations.order_by("-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise,
                _operation_status_label(operation),
                operation.date_transmission_depot.strftime("%d/%m/%Y") if operation.date_transmission_depot else "-",
                operation.date_reception_transitaire.strftime("%d/%m/%Y") if operation.date_reception_transitaire else "-",
                operation.date_bons_declares.strftime("%d/%m/%Y") if operation.date_bons_declares else "-",
                operation.date_bons_liquides.strftime("%d/%m/%Y") if operation.date_bons_liquides else "-",
            ]
        )
    return _build_operations_pdf_response(
        "rapport_transitaire.pdf",
        rows,
        ["BL", "Commande", "Client", "Etat", "Transmission", "Reception", "Declaration", "Liquidation"],
        "Rapport transitaire",
    )


def export_transitaire_historique_xls(request):
    operations, _, _, _, _ = _transitaire_history_queryset(request)
    rows = []
    for operation in operations.order_by("-date_reception_logistique", "-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise if operation.client else "",
                _operation_status_label(operation),
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                operation.date_reception_logistique.strftime("%Y-%m-%d") if operation.date_reception_logistique else "",
                operation.remis_a_nom or "",
                operation.remis_a_telephone or "",
                operation.date_bons_charges.strftime("%Y-%m-%d") if operation.date_bons_charges else "",
                operation.date_bons_livres.strftime("%Y-%m-%d") if operation.date_bons_livres else "",
                operation.date_bon_retour.strftime("%Y-%m-%d") if operation.date_bon_retour else "",
            ]
        )
    return _build_operations_excel_response(
        "historique_transitaire.xlsx",
        rows,
        [
            "BL",
            "Commande",
            "Client",
            "Etat",
            "Camion",
            "Chauffeur",
            "Reception logistique",
            "Remis a",
            "Telephone",
            "Date charge",
            "Date livre",
            "Bon retour",
        ],
        "Historique transitaire",
    )


def export_transitaire_historique_pdf(request):
    operations, _, _, _, _ = _transitaire_history_queryset(request)
    rows = []
    for operation in operations.order_by("-date_reception_logistique", "-date_creation"):
        rows.append(
            [
                operation.numero_bl,
                operation.commande.reference if operation.commande else "",
                operation.client.entreprise if operation.client else "",
                _operation_status_label(operation),
                operation.date_reception_logistique.strftime("%d/%m/%Y") if operation.date_reception_logistique else "-",
                operation.remis_a_nom or "-",
                operation.date_bons_charges.strftime("%d/%m/%Y") if operation.date_bons_charges else "-",
                operation.date_bons_livres.strftime("%d/%m/%Y") if operation.date_bons_livres else "-",
                operation.date_bon_retour.strftime("%d/%m/%Y") if operation.date_bon_retour else "-",
            ]
        )
    return _build_operations_pdf_response(
        "historique_transitaire.pdf",
        rows,
        ["BL", "Commande", "Client", "Etat", "Reception", "Remis a", "Charge", "Livre", "Bon retour"],
        "Historique transitaire",
    )


def historique_transitaire_operations(request):
    operations, query, etat, date_from, date_to = _transitaire_history_queryset(request)
    operations_history = list(operations.order_by("-date_reception_logistique", "-date_creation"))
    for operation in operations_history:
        operation.status_label = _operation_status_label(operation)

    total_history = len(operations_history)
    total_loaded = sum(1 for operation in operations_history if operation.etat_bon == "charge")
    total_delivered = sum(1 for operation in operations_history if operation.etat_bon == "livre")
    total_returned = sum(1 for operation in operations_history if operation.date_bon_retour)

    return render(
        request,
        "operations/transitaire_historique.html",
        {
            "operations_history": operations_history,
            "query": query,
            "etat": etat,
            "date_from": date_from,
            "date_to": date_to,
            "total_history": total_history,
            "total_loaded": total_loaded,
            "total_delivered": total_delivered,
            "total_returned": total_returned,
            "current_filters": request.GET.urlencode(),
            "active_tab": "transitaire_historique",
        },
    )


def valider_reception_transitaire(request):
    if request.method != "POST":
        return redirect("transitaire_operations")

    operation_id = request.POST.get("operation_id")
    selected_ids = request.POST.getlist("selected_operations")
    date_raw = (request.POST.get("date_action") or "").strip()
    if selected_ids:
        target_ids = selected_ids
    elif operation_id:
        target_ids = [operation_id]
    else:
        messages.error(request, "Aucun BL selectionne pour la reception transitaire.")
        return redirect("transitaire_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date de reception transitaire.")
        return redirect("transitaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("transitaire_operations")

    updated_count = 0
    for operation in Operation.objects.filter(id__in=target_ids):
        if operation.etat_bon != "attente_reception_transitaire":
            continue
        if not operation.date_transmission_depot:
            continue
        operation.date_reception_transitaire = action_date
        operation.etat_bon = "transmis"
        try:
            operation.full_clean()
            operation.save()
            updated_count += 1
        except ValidationError as exc:
            _push_validation_errors(request, exc)

    if updated_count:
        if updated_count == 1:
            messages.success(request, "La reception transitaire a bien ete validee.")
        else:
            messages.success(request, f"{updated_count} BL ont bien ete recus par le transitaire.")
    else:
        messages.error(request, "Aucun BL selectionne n'est eligible a la reception transitaire.")

    return redirect("transitaire_operations")


def _apply_transitaire_bulk_action(request, operations, action_name, action_date):
    updated_count = 0
    skipped_count = 0

    for operation in operations:
        if _operation_is_locked_after_charge(operation):
            skipped_count += 1
            continue

        if action_name == "declare":
            if operation.etat_bon != "transmis" or not operation.date_reception_transitaire:
                skipped_count += 1
                continue
            operation.etat_bon = "declare"
            operation.date_bons_declares = action_date
        elif action_name == "liquide":
            if operation.etat_bon != "declare":
                skipped_count += 1
                continue
            operation.etat_bon = "liquide"
            operation.date_bons_liquides = action_date
        elif action_name == "transferer-logistique":
            if operation.etat_bon != "liquide":
                skipped_count += 1
                continue
            operation.date_transfert_logistique = action_date
            operation.etat_bon = "attente_reception_logistique"
        else:
            skipped_count += 1
            continue

        try:
            operation.full_clean()
            if action_name == "liquide":
                _decrement_sommier_stock_on_liquidation(operation)
            operation.save()
            updated_count += 1
        except ValidationError as exc:
            skipped_count += 1
            _push_validation_errors(request, exc)

    return updated_count, skipped_count


def action_groupee_transitaire(request, action_name):
    if request.method != "POST":
        return redirect("transitaire_operations")

    selected_ids = request.POST.getlist("selected_operations")
    date_raw = (request.POST.get("date_action") or "").strip()

    if not selected_ids:
        messages.error(request, "Aucun BL selectionne pour cette action groupee.")
        return redirect("transitaire_operations")
    if not date_raw:
        messages.error(request, "Merci de renseigner la date de l'action transitaire.")
        return redirect("transitaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("transitaire_operations")

    operations = Operation.objects.filter(id__in=selected_ids).order_by("id")
    updated_count, skipped_count = _apply_transitaire_bulk_action(request, operations, action_name, action_date)

    labels = {
        "declare": "declares",
        "liquide": "liquides",
        "transferer-logistique": "transferes a la logistique",
    }
    action_label = labels.get(action_name, "traites")

    if updated_count:
        messages.success(
            request,
            f"{updated_count} BL ont bien ete {action_label}."
            + (f" {skipped_count} BL non eligibles ont ete ignores." if skipped_count else ""),
        )
    else:
        messages.error(request, "Aucun BL selectionne n'est eligible pour cette action.")

    return redirect("transitaire_operations")


def changer_etat_transitaire(request, id, etat):
    if request.method != "POST":
        return redirect("transitaire_operations")

    operation = get_object_or_404(Operation, id=id)
    date_raw = (request.POST.get("date_action") or "").strip()
    action_date = None

    if _operation_is_locked_after_charge(operation):
        messages.error(request, "Les dates transitaires ne peuvent plus etre modifiees une fois le BL charge.")
        return redirect("transitaire_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date.")
        return redirect("transitaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("transitaire_operations")

    if etat == "declare":
        if operation.etat_bon != "transmis":
            messages.error(request, "Le BL doit d'abord etre transmis avant la declaration.")
            return redirect("transitaire_operations")
        if not operation.date_reception_transitaire:
            messages.error(request, "Le transitaire doit d'abord valider la reception du BL.")
            return redirect("transitaire_operations")
        operation.etat_bon = "declare"
        operation.date_bons_declares = action_date
    elif etat == "liquide":
        if operation.etat_bon != "declare":
            messages.error(request, "Le BL doit d'abord etre declare avant la liquidation.")
            return redirect("transitaire_operations")
        operation.etat_bon = "liquide"
        operation.date_bons_liquides = action_date
    else:
        messages.error(request, "Action transitaire inconnue.")
        return redirect("transitaire_operations")
    try:
        operation.full_clean()
        if etat == "liquide":
            _decrement_sommier_stock_on_liquidation(operation)
        operation.save()
    except ValidationError as exc:
        _push_validation_errors(request, exc)

    return redirect("transitaire_operations")


def transferer_liquide_logistique(request, id):
    if request.method != "POST":
        return redirect("transitaire_operations")

    operation = get_object_or_404(Operation, id=id)
    date_raw = (request.POST.get("date_action") or "").strip()

    if operation.etat_bon != "liquide":
        messages.error(request, "Seuls les BL liquides peuvent etre transferes au logisticien.")
        return redirect("transitaire_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date de transfert au logisticien.")
        return redirect("transitaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("transitaire_operations")

    operation.date_transfert_logistique = action_date
    operation.etat_bon = "attente_reception_logistique"
    try:
        operation.full_clean()
        operation.save()
        messages.success(request, "Le BL liquide a bien ete transfere au logisticien.")
    except ValidationError as exc:
        _push_validation_errors(request, exc)

    return redirect("transitaire_operations")


def charger_bon_direct(request, id):
    if request.method != "POST":
        return redirect("transitaire_operations")

    operation = get_object_or_404(Operation, id=id)
    date_raw = (request.POST.get("date_action") or "").strip()

    if operation.etat_bon != "liquide":
        messages.error(request, "Le chargement direct n'est possible qu'apres liquidation.")
        return redirect("transitaire_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date de chargement.")
        return redirect("transitaire_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("transitaire_operations")

    operation.date_bons_charges = action_date
    operation.etat_bon = "charge"
    try:
        operation.full_clean()
        operation.save()
        messages.success(request, "Le BL a ete charge directement apres liquidation.")
    except ValidationError as exc:
        _push_validation_errors(request, exc)

    return redirect("transitaire_operations")


def _decorate_logistique_operation(operation):
    depenses_chargement = list(
        _depenses_chargement_queryset_for_operation(operation)
    )
    operation.depenses_chargement_items = depenses_chargement
    montant_depenses = sum(Decimal(depense.montant_total or depense.montant_estime or 0) for depense in depenses_chargement)
    operation.montant_depenses_chargement = montant_depenses
    operation.montant_depenses_display = (
        _format_amount(montant_depenses) + " GNF"
        if montant_depenses > 0
        else ""
    )
    if not depenses_chargement:
        operation.depenses_stage_key = "logistique"
        operation.depenses_status_label = "Chez logistique"
        operation.depenses_status_note = "Aucune depense rattachee"
        operation.depenses_status_variant = "danger"
    elif any(
        depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
        for depense in depenses_chargement
    ):
        operation.depenses_stage_key = "dga"
        operation.depenses_status_label = "Chez DGA"
        operation.depenses_status_note = operation.montant_depenses_display
        operation.depenses_status_variant = "warning"
    elif any(
        depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
        for depense in depenses_chargement
    ):
        operation.depenses_stage_key = "dg"
        operation.depenses_status_label = "Chez DG"
        operation.depenses_status_note = operation.montant_depenses_display
        operation.depenses_status_variant = "warning"
    elif any(
        depense.statut in {
            Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        }
        for depense in depenses_chargement
    ):
        operation.depenses_stage_key = "paiement"
        operation.depenses_status_label = "Chez paiement"
        operation.depenses_status_note = operation.montant_depenses_display
        operation.depenses_status_variant = "info"
    elif any(
        depense.statut == Depense.STATUT_PAYEE
        for depense in depenses_chargement
    ):
        operation.depenses_stage_key = "payee"
        operation.depenses_status_label = "Payee"
        operation.depenses_status_note = operation.montant_depenses_display
        operation.depenses_status_variant = "success"
    else:
        operation.depenses_stage_key = "paiement"
        operation.depenses_status_label = "Chez paiement"
        operation.depenses_status_note = operation.montant_depenses_display
        operation.depenses_status_variant = "info"
    operation.status_label = _operation_status_label(operation)
    operation.can_validate_logistique = operation.etat_bon == "attente_reception_logistique"
    operation.can_remettre_chauffeur = operation.etat_bon == "liquide_logistique"
    return operation


def _logisticien_queryset(request):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    depense_niveau = request.GET.get("depense_niveau", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    operations = (
        Operation.objects.select_related("client", "camion", "chauffeur", "commande", "produit")
        .annotate(commande_reference_compact=Replace("commande__reference", Value(" "), Value("")))
        .filter(etat_bon__in=(ETATS_LOGISTIQUE_RECEPTION | ETATS_LOGISTIQUE_TRAITEMENT))
    )
    if query:
        compact_query = "".join(query.split())
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(commande_reference_compact__icontains=compact_query)
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_from:
        operations = operations.filter(date_reception_logistique__gte=date_from)
    if date_to:
        operations = operations.filter(date_reception_logistique__lte=date_to)

    operation_list = list(operations.order_by("commande_id", "date_creation", "numero_bl"))
    operation_list = [_decorate_logistique_operation(operation) for operation in operation_list]
    if depense_niveau:
        operation_list = [operation for operation in operation_list if operation.depenses_stage_key == depense_niveau]
    _annotate_commande_groups(operation_list)
    _apply_logistique_group_expense_display(operation_list)
    return operation_list, query, etat, depense_niveau, date_from, date_to


def export_logisticien_xls(request):
    operations, _, _, _, _, _ = _logisticien_queryset(request)
    rows = []
    for operation in operations:
        detail_depense = (
            operation.depenses_display_amount
            or operation.depenses_shared_note
            or operation.depenses_status_note
            or ""
        )
        rows.append(
            [
                operation.numero_bl,
                operation.client.entreprise if operation.client else "",
                operation.camion.numero_tracteur if operation.camion else "",
                operation.chauffeur.nom if operation.chauffeur else "",
                str(operation.quantite or ""),
                operation.status_label,
                operation.date_reception_logistique.strftime("%Y-%m-%d") if operation.date_reception_logistique else "",
                operation.date_bons_charges.strftime("%Y-%m-%d") if operation.date_bons_charges else "",
                operation.date_bons_livres.strftime("%Y-%m-%d") if operation.date_bons_livres else "",
                operation.depenses_status_label,
                detail_depense,
            ]
        )
    return _build_operations_excel_response(
        "rapport_logistique.xlsx",
        rows,
        ["BL", "Client", "Camion", "Chauffeur", "Quantite", "Etat BL", "Reception logistique", "Date charge", "Date livre", "Niveau depense BL", "Detail"],
        "Logistique BL",
    )


def export_logisticien_pdf(request):
    operations, _, _, _, _, _ = _logisticien_queryset(request)
    rows = []
    for operation in operations:
        detail_depense = (
            operation.depenses_display_amount
            or operation.depenses_shared_note
            or operation.depenses_status_note
            or "-"
        )
        rows.append(
            [
                operation.numero_bl,
                operation.client.entreprise if operation.client else "",
                operation.status_label,
                operation.date_reception_logistique.strftime("%d/%m/%Y") if operation.date_reception_logistique else "-",
                operation.date_bons_charges.strftime("%d/%m/%Y") if operation.date_bons_charges else "-",
                operation.date_bons_livres.strftime("%d/%m/%Y") if operation.date_bons_livres else "-",
                operation.depenses_status_label,
                detail_depense,
            ]
        )
    return _build_operations_pdf_response(
        "rapport_logistique.pdf",
        rows,
        ["BL", "Client", "Etat BL", "Reception", "Charge", "Livre", "Niveau depense", "Detail"],
        "Rapport logistique BL",
    )


def logisticien_operations(request):
    operation_list, query, etat, depense_niveau, date_from, date_to = _logisticien_queryset(request)

    total_receptions_logistique = sum(1 for operation in operation_list if operation.can_validate_logistique)
    depenses_uniques = {}
    for operation in operation_list:
        for depense in getattr(operation, "depenses_chargement_items", []):
            depenses_uniques[depense.id] = depense
    total_montant_depenses = sum(
        (Decimal(depense.montant_total or depense.montant_estime or 0) for depense in depenses_uniques.values()),
        Decimal("0"),
    )
    total_quantite = sum((Decimal(operation.quantite or 0) for operation in operation_list), Decimal("0"))
    total_gasoil = sum(
        (Decimal(operation.quantite or 0) for operation in operation_list if operation.produit and "gas" in (operation.produit.nom or "").lower()),
        Decimal("0"),
    )
    total_essence = sum(
        (Decimal(operation.quantite or 0) for operation in operation_list if operation.produit and "ess" in (operation.produit.nom or "").lower()),
        Decimal("0"),
    )

    return render(
        request,
        "operations/logisticien.html",
        {
            "operations": operation_list,
            "query": query,
            "etat": etat,
            "depense_niveau": depense_niveau,
            "date_from": date_from,
            "date_to": date_to,
            "active_tab": "logisticien",
            "page_title": "Suivi logistique BL",
            "update_url_prefix": "/operations/logisticien/",
            "total_receptions_logistique": total_receptions_logistique,
            "default_reception_logistique_date": timezone.localdate().isoformat(),
            "total_montant_depenses_display": _format_amount(total_montant_depenses) + " GNF",
            "total_quantite_display": _format_amount(total_quantite),
            "total_gasoil_display": _format_amount(total_gasoil),
            "total_essence_display": _format_amount(total_essence),
            "current_filters": request.GET.urlencode(),
        },
    )


def valider_receptions_logistiques(request):
    if request.method != "POST":
        return redirect("logisticien_operations")

    selected_ids = request.POST.getlist("selected_operations")
    date_raw = (request.POST.get("date_reception_logistique") or "").strip()

    if not selected_ids:
        messages.error(request, "Selectionnez au moins un BL a receptionner.")
        return redirect("logisticien_operations")

    if not date_raw:
        messages.error(request, "Merci de renseigner la date de reception logistique.")
        return redirect("logisticien_operations")

    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("logisticien_operations")

    base_operations = list(
        Operation.objects.select_related("commande").filter(id__in=selected_ids, etat_bon="attente_reception_logistique")
    )
    expanded_ids = set()
    for operation in base_operations:
        sibling_ids = _commande_sibling_operations(operation, expected_state="attente_reception_logistique").values_list("id", flat=True)
        expanded_ids.update(sibling_ids)
    operations = Operation.objects.filter(id__in=expanded_ids or selected_ids, etat_bon="attente_reception_logistique")
    if not operations.exists():
        messages.error(request, "Aucun BL selectionne n'est en attente de reception logistique.")
        return redirect("logisticien_operations")

    updated = 0
    for operation in operations:
        operation.date_reception_logistique = action_date
        operation.etat_bon = "liquide_logistique"
        try:
            operation.full_clean()
            operation.save()
            updated += 1
        except ValidationError as exc:
            _push_validation_errors(request, exc)

    if updated:
        messages.success(request, f"{updated} BL ont bien ete receptionnes par la logistique.")

    return redirect("logisticien_operations")


def _chef_chauffeur_queryset(request, historique=False):
    query = request.GET.get("q", "").strip()
    etat = request.GET.get("etat", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    etats_cibles = ETATS_CHEF_CHAUFFEUR_HISTORIQUE if historique else ETATS_CHEF_CHAUFFEUR
    operations = Operation.objects.select_related("client", "camion", "chauffeur").filter(
        etat_bon__in=etats_cibles
    )
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(camion__numero_citerne__icontains=query)
            | Q(chauffeur__nom__icontains=query)
            | Q(commande__reference__icontains=query)
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_from:
        if historique:
            operations = operations.filter(date_bons_livres__gte=date_from)
        else:
            operations = operations.filter(date_creation__date__gte=date_from)
    if date_to:
        if historique:
            operations = operations.filter(date_bons_livres__lte=date_to)
        else:
            operations = operations.filter(date_creation__date__lte=date_to)
    return operations, query, etat, date_from, date_to


def chef_chauffeur_operations(request):
    operations, query, etat, date_from, date_to = _chef_chauffeur_queryset(request)
    operation_list = list(operations.order_by("-date_creation"))
    for operation in operation_list:
        operation.status_label = _operation_status_label(operation)
        operation.next_action = "charge" if operation.etat_bon == "liquide_chauffeur" else "livre"
        operation.next_action_label = "Charge" if operation.etat_bon == "liquide_chauffeur" else "Livre"
        depenses_chargement = list(
            _depenses_chargement_queryset_for_operation(operation)
        )
        if not depenses_chargement:
            operation.depenses_status_label = "Aucune depense"
            operation.depenses_status_variant = "danger"
        elif any(
            depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
            for depense in depenses_chargement
        ):
            operation.depenses_status_label = "Chez logisticien"
            operation.depenses_status_variant = "warning"
        elif any(
            depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
            for depense in depenses_chargement
        ):
            operation.depenses_status_label = "Chez DGA"
            operation.depenses_status_variant = "warning"
        elif any(
            depense.statut in {
                Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            }
            for depense in depenses_chargement
        ):
            operation.depenses_status_label = "Chez DG"
            operation.depenses_status_variant = "info"
        elif any(
            depense.statut == Depense.STATUT_PAYEE
            for depense in depenses_chargement
        ):
            operation.depenses_status_label = "Payee"
            operation.depenses_status_variant = "success"
        else:
            operation.depenses_status_label = "Suivi en cours"
            operation.depenses_status_variant = "success"

    return render(
        request,
        "operations/chef_chauffeur.html",
        {
            "operations": operation_list,
            "query": query,
            "etat": etat,
            "date_from": date_from,
            "date_to": date_to,
            "active_tab": "chef_chauffeur",
            "page_title": "Suivi chef chauffeur",
            "etat_options_chef_chauffeur": [
                ("liquide_chauffeur", "En attente de chargement"),
                ("charge", "Charge"),
            ],
            "total_waiting_load": sum(1 for operation in operation_list if operation.etat_bon == "liquide_chauffeur"),
            "total_loaded": sum(1 for operation in operation_list if operation.etat_bon == "charge"),
            "default_action_date": timezone.localdate().isoformat(),
        },
    )


def historique_chef_chauffeur_operations(request):
    operations, query, etat, date_from, date_to = _chef_chauffeur_queryset(request, historique=True)
    operation_list = list(operations.order_by("-date_bons_livres", "-date_creation"))
    for operation in operation_list:
        operation.status_label = _operation_status_label(operation)

    return render(
        request,
        "operations/chef_chauffeur_historique.html",
        {
            "operations": operation_list,
            "query": query,
            "etat": etat,
            "date_from": date_from,
            "date_to": date_to,
            "active_tab": "chef_chauffeur_historique",
            "page_title": "Historique chauffeur",
            "total_delivered": len(operation_list),
            "total_returned": sum(1 for operation in operation_list if operation.date_bon_retour),
        },
    )


def action_chef_chauffeur(request, id, action):
    if request.method != "POST":
        return redirect("chef_chauffeur_operations")

    operation = get_object_or_404(Operation, id=id)
    date_raw = (request.POST.get("date_action") or "").strip()
    if not date_raw:
        messages.error(request, "Merci de renseigner la date de l'action chauffeur.")
        return redirect("chef_chauffeur_operations")
    try:
        action_date = date.fromisoformat(date_raw)
    except ValueError:
        messages.error(request, "La date saisie est invalide.")
        return redirect("chef_chauffeur_operations")

    quantite_livree = None
    if action == "livre":
        quantite_raw = (request.POST.get("quantite_livree") or "").strip()
        if not quantite_raw:
            messages.error(request, "La quantite livree est obligatoire pour marquer le BL comme livre.")
            return redirect("chef_chauffeur_operations")
        normalized_quantite = "".join(quantite_raw.split()).replace(",", ".")
        try:
            quantite_livree = Decimal(normalized_quantite)
        except Exception:
            messages.error(request, "La quantite livree saisie est invalide.")
            return redirect("chef_chauffeur_operations")

    if action == "charge":
        if operation.etat_bon != "liquide_chauffeur":
            messages.error(request, "Ce BL n'est pas en attente de chargement.")
            return redirect("chef_chauffeur_operations")
        operation.etat_bon = "charge"
        operation.date_bons_charges = action_date
        success_message = "Le BL a bien ete marque comme charge."
    elif action == "livre":
        if operation.etat_bon != "charge":
            messages.error(request, "Seuls les BL charges peuvent etre livres.")
            return redirect("chef_chauffeur_operations")
        operation.etat_bon = "livre"
        operation.date_bons_livres = action_date
        operation.quantite_livree = quantite_livree
        if not operation.date_bons_charges:
            operation.date_bons_charges = action_date
        success_message = "Le BL a bien ete marque comme livre."
    else:
        messages.error(request, "Action chauffeur inconnue.")
        return redirect("chef_chauffeur_operations")

    try:
        operation.full_clean()
        operation.save()
        messages.success(request, success_message)
    except ValidationError as exc:
        _push_validation_errors(request, exc)
    return redirect("chef_chauffeur_operations")


def modifier_operation_logisticien(request, id):
    operation = get_object_or_404(Operation, id=id)
    user_role = get_user_role(request.user)
    is_logistique = user_role == "logistique" or bool(getattr(request.user, "is_superuser", False))
    is_chef_chauffeur = user_role == "chef_chauffeur"
    if not (is_logistique or is_chef_chauffeur):
        messages.error(request, "Vous n'avez pas acces a cette page.")
        return redirect("/dashboard/")
    redirect_view_name = "modifier_operation_chef_chauffeur" if is_chef_chauffeur else "modifier_operation_logisticien"
    list_view_name = "chef_chauffeur_operations" if is_chef_chauffeur else "logisticien_operations"
    if is_chef_chauffeur:
        messages.info(request, "Le suivi chauffeur se fait maintenant directement depuis le tableau Charge / Livre.")
        return redirect(list_view_name)
    if operation.remplace_par_id:
        messages.error(request, "Cet ancien BL n'est plus modifiable, car le camion a deja ete change.")
        return redirect(list_view_name)
    if is_chef_chauffeur and operation.etat_bon == "livre":
        messages.error(request, "Ce BL est deja livre. Le chef chauffeur ne peut plus le mettre a jour.")
        return redirect(list_view_name)
    if request.method == "POST":
        post_data = request.POST.copy()
        workflow_action = (post_data.get("workflow_action") or "").strip()

        if workflow_action == "reception_logistique":
            if not is_logistique:
                messages.error(request, "Seule la logistique peut valider la reception du BL.")
                return redirect(redirect_view_name, id=operation.id)
            date_raw = (post_data.get("workflow_date") or "").strip()
            if operation.etat_bon != "attente_reception_logistique":
                messages.error(request, "Ce BL n'est pas en attente de validation reception logistique.")
                return redirect(redirect_view_name, id=operation.id)
            if not date_raw:
                messages.error(request, "Merci de renseigner la date de reception logistique.")
                return redirect(redirect_view_name, id=operation.id)
            try:
                action_date = date.fromisoformat(date_raw)
            except ValueError:
                messages.error(request, "La date saisie est invalide.")
                return redirect(redirect_view_name, id=operation.id)
            target_operations = list(_commande_sibling_operations(operation, expected_state="attente_reception_logistique"))
            updated = 0
            for target in target_operations:
                target.date_reception_logistique = action_date
                target.etat_bon = "liquide_logistique"
                try:
                    target.full_clean()
                    target.save()
                    updated += 1
                except ValidationError as exc:
                    _push_validation_errors(request, exc)
            if updated:
                messages.success(
                    request,
                    "La reception logistique a bien ete validee."
                    if updated == 1
                    else f"La reception logistique a bien ete validee sur {updated} BL de la meme commande."
                )
            return redirect(redirect_view_name, id=operation.id)

        if workflow_action == "remise_chauffeur":
            if not is_logistique:
                messages.error(request, "Seule la logistique peut remettre le BL au chauffeur.")
                return redirect(redirect_view_name, id=operation.id)
            date_raw = (post_data.get("workflow_date") or "").strip()
            remis_a_nom = (post_data.get("remis_a_nom") or "").strip()
            remis_a_telephone = (post_data.get("remis_a_telephone") or "").strip()
            if operation.etat_bon != "liquide_logistique":
                messages.error(request, "Le BL doit d'abord etre recu par la logistique.")
                return redirect(redirect_view_name, id=operation.id)
            if not (date_raw and remis_a_nom and remis_a_telephone):
                messages.error(request, "Le nom, le telephone et la date de remise sont obligatoires.")
                return redirect(redirect_view_name, id=operation.id)
            try:
                action_date = date.fromisoformat(date_raw)
            except ValueError:
                messages.error(request, "La date saisie est invalide.")
                return redirect(redirect_view_name, id=operation.id)
            target_operations = list(_commande_sibling_operations(operation, expected_state="liquide_logistique"))
            updated = 0
            for target in target_operations:
                target.date_remise_chauffeur = action_date
                target.remis_a_nom = remis_a_nom
                target.remis_a_telephone = remis_a_telephone
                target.etat_bon = "liquide_chauffeur"
                try:
                    target.full_clean()
                    target.save()
                    updated += 1
                except ValidationError as exc:
                    _push_validation_errors(request, exc)
            if updated:
                messages.success(
                    request,
                    "Le BL liquide a bien ete remis pour chargement."
                    if updated == 1
                    else f"Les {updated} BL de la meme commande ont bien recu la meme remise chauffeur."
                )
            return redirect(redirect_view_name, id=operation.id)

        if operation.date_bons_charges and not post_data.get("date_bons_charges"):
            post_data["date_bons_charges"] = operation.date_bons_charges.isoformat()
        if operation.date_bons_livres and not post_data.get("date_bons_livres"):
            post_data["date_bons_livres"] = operation.date_bons_livres.isoformat()
        if operation.date_bon_retour and not post_data.get("date_bon_retour"):
            post_data["date_bon_retour"] = operation.date_bon_retour.isoformat()
        if operation.quantite_livree is not None and not post_data.get("quantite_livree"):
            post_data["quantite_livree"] = str(operation.quantite_livree)
        if post_data.get("etat_bon") == "charge" and not post_data.get("date_bons_charges"):
            post_data["date_bons_charges"] = timezone.localdate().isoformat()
        if post_data.get("etat_bon") == "livre" and not post_data.get("date_bons_charges"):
            post_data["date_bons_charges"] = (
                operation.date_bons_charges.isoformat()
                if operation.date_bons_charges
                else post_data.get("date_bons_livres")
                or timezone.localdate().isoformat()
            )
        if post_data.get("etat_bon") == "retour" and not post_data.get("date_bons_livres"):
            post_data["date_bons_livres"] = (
                operation.date_bons_livres.isoformat()
                if operation.date_bons_livres
                else post_data.get("date_bon_retour")
                or timezone.localdate().isoformat()
            )

        requested_etat = (post_data.get("etat_bon") or "").strip()
        requested_retour = bool(post_data.get("date_bon_retour"))
        if is_logistique and requested_etat in {"charge", "livre"} and not requested_retour:
            messages.error(request, "Le chargement et la livraison relevent du chef chauffeur.")
            return redirect(redirect_view_name, id=operation.id)
        if is_chef_chauffeur and workflow_action:
            messages.error(request, "Cette action releve de la logistique.")
            return redirect(redirect_view_name, id=operation.id)
        if is_chef_chauffeur and requested_retour:
            messages.error(request, "Le bon retour releve de la logistique.")
            return redirect(redirect_view_name, id=operation.id)
        if is_chef_chauffeur:
            post_data["mouvement_camion"] = operation.mouvement_camion or ""
            post_data["latitude_position"] = operation.latitude_position or ""
            post_data["longitude_position"] = operation.longitude_position or ""
            post_data["observation"] = operation.observation or ""

        form = LogisticienOperationForm(post_data, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.etat_bon == "charge" and not operation.date_bons_charges:
                operation.date_bons_charges = timezone.localdate()
            if operation.etat_bon == "charge" and operation.pk and operation.etat_bon == "charge":
                pass
            if operation.etat_bon == "livre" and not operation.date_bons_charges:
                operation.date_bons_charges = operation.date_bons_livres or timezone.localdate()
            if operation.etat_bon == "livre" and not operation.date_bons_livres:
                operation.date_bons_livres = timezone.localdate()
            if operation.date_bon_retour and not operation.date_bons_livres:
                operation.date_bons_livres = operation.date_bon_retour
            try:
                operation.full_clean()
                operation.save()
            except ValidationError as exc:
                _push_validation_errors(request, exc)
                form.add_error(None, "Le circuit BL ne permet pas cette action pour l'instant.")
                return render(
                    request,
                    "operations/logisticien_form.html",
                    {
                        "form": form,
                        "operation": operation,
                        "depenses_camion": [],
                        "commande_multi_bl": bool(operation.commande_id and operation.commande.operations.filter(remplace_par__isnull=True).count() > 1),
                        "commande_operations_group": list(
                            operation.commande.operations.filter(remplace_par__isnull=True)
                            .select_related("camion", "chauffeur")
                            .order_by("date_creation", "numero_bl", "id")
                        ) if operation.commande_id else [],
                        "can_manage_depenses_camion": bool(is_logistique and (operation.date_bons_charges or operation.etat_bon in {"charge", "livre"})),
                        "can_validate_reception_logistique": bool(is_logistique and operation.etat_bon == "attente_reception_logistique"),
                        "can_remettre_chauffeur": bool(is_logistique and operation.etat_bon == "liquide_logistique"),
                        "can_mark_charge": bool(is_chef_chauffeur and operation.etat_bon == "liquide_chauffeur"),
                        "can_mark_livre": bool(is_chef_chauffeur and operation.etat_bon == "charge" and not operation.date_bon_retour),
                        "can_mark_retour": bool(is_logistique and operation.etat_bon == "livre" and not operation.date_bon_retour),
                        "can_edit_suivi_camion": bool(is_logistique and operation.etat_bon in {"charge", "livre"}),
                        "active_tab": "chef_chauffeur" if is_chef_chauffeur else "logisticien",
                        "workflow_role_label": "Chef chauffeur" if is_chef_chauffeur else "Logistique",
                    },
                )
            return redirect(redirect_view_name, id=operation.id)
    else:
        form = LogisticienOperationForm(instance=operation)

    can_edit_suivi_camion = bool(is_logistique and operation.etat_bon in {"charge", "livre"})
    if not can_edit_suivi_camion:
        form.fields["mouvement_camion"].widget.attrs["readonly"] = True
        form.fields["latitude_position"].widget.attrs["readonly"] = True
        form.fields["longitude_position"].widget.attrs["readonly"] = True
        form.fields["observation"].widget.attrs["readonly"] = True

    depenses_camion = list(
        _depenses_chargement_queryset_for_operation(operation)
        .select_related("type_depense", "demandeur", "commande")
        .order_by("-date_creation")
    )
    commande_operations_group = []
    if operation.commande_id:
        commande_operations_group = list(
            operation.commande.operations.filter(remplace_par__isnull=True)
            .select_related("camion", "chauffeur")
            .order_by("date_creation", "numero_bl", "id")
        )
    dga_decision_exists = any(depense.expression_decision_dga for depense in depenses_camion)
    commande_multi_bl = len(commande_operations_group) > 1
    for depense in depenses_camion:
        depense.montant_affiche = _format_amount(depense.montant_total)
        depense.portee_affichee = "Commande" if depense.portee_chargement == Depense.PORTEE_COMMANDE else "BL"
        depense.portee_badge_class = "status-info" if depense.portee_chargement == Depense.PORTEE_COMMANDE else "status-ok"
        depense.scope_detail = (
            f"{depense.commande.reference} ({depense.commande.operations.filter(remplace_par__isnull=True).count()} BL)"
            if depense.portee_chargement == Depense.PORTEE_COMMANDE and depense.commande_id
            else operation.numero_bl
        )
        depense.can_edit_by_logistique = bool(
            is_logistique
            and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
            and not depense.expression_decision_dga
        )
        premiere_ligne = depense.lignes.order_by("id").first()
        if premiere_ligne:
            depense.edit_url = f"/depenses/chargement/{operation.id}/ligne/{premiere_ligne.id}/modifier/"
        else:
            depense.edit_url = f"/depenses/chargement/{operation.id}/ajouter/"

    return render(
        request,
        "operations/logisticien_form.html",
        {
            "form": form,
            "operation": operation,
            "depenses_camion": depenses_camion,
            "can_manage_depenses_camion": bool(is_logistique and (operation.date_bons_charges or operation.etat_bon in {"charge", "livre"})),
            "can_add_depenses_camion": bool(
                is_logistique
                and (operation.date_bons_charges or operation.etat_bon in {"charge", "livre"})
                and not dga_decision_exists
            ),
            "commande_multi_bl": commande_multi_bl,
            "commande_operations_group": commande_operations_group,
            "depenses_camion_locked_by_dga": dga_decision_exists,
            "can_validate_reception_logistique": bool(is_logistique and operation.etat_bon == "attente_reception_logistique"),
            "can_remettre_chauffeur": bool(is_logistique and operation.etat_bon == "liquide_logistique"),
            "can_mark_charge": bool(is_chef_chauffeur and operation.etat_bon == "liquide_chauffeur"),
            "can_mark_livre": bool(is_chef_chauffeur and operation.etat_bon == "charge" and not operation.date_bon_retour),
            "can_mark_retour": bool(is_logistique and operation.etat_bon == "livre" and not operation.date_bon_retour),
            "can_edit_suivi_camion": can_edit_suivi_camion,
            "active_tab": "chef_chauffeur" if is_chef_chauffeur else "logisticien",
            "workflow_role_label": "Chef chauffeur" if is_chef_chauffeur else "Logistique",
        },
    )


def ajouter_produit_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = ProduitForm(request.POST)
    if form.is_valid():
        produit = form.save()
        return JsonResponse(
            {
                "success": True,
                "produit": {
                    "id": produit.id,
                    "label": produit.nom,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_regime_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = RegimeDouanierForm(request.POST)
    if form.is_valid():
        regime = form.save()
        return JsonResponse(
            {
                "success": True,
                "regime": {
                    "id": regime.id,
                    "label": f"{regime.libelle} ({regime.code_regime})",
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_depot_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = DepotForm(request.POST)
    if form.is_valid():
        depot = form.save()
        return JsonResponse(
            {
                "success": True,
                "depot": {
                    "id": depot.id,
                    "label": depot.nom,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def chauffeur_par_camion(request):
    camion_id = request.GET.get("camion_id")
    if not camion_id:
        return JsonResponse({"success": True, "chauffeur": None})

    from chauffeurs.models import Chauffeur

    chauffeur = Chauffeur.objects.filter(camion_id=camion_id).order_by("nom").first()
    if not chauffeur:
        return JsonResponse({"success": True, "chauffeur": None})

    return JsonResponse(
        {
            "success": True,
            "chauffeur": {
                "id": chauffeur.id,
                "nom": chauffeur.nom,
            },
        }
    )


def commande_infos(request):
    commande_id = request.GET.get("commande_id")
    if not commande_id:
        return JsonResponse(
            {"success": False, "errors": {"commande": ["Commande manquante."]}},
            status=400,
        )

    from commandes.models import Commande

    commande = get_object_or_404(
        Commande.objects.select_related("client", "produit", "camion", "chauffeur"),
        id=commande_id,
    )
    return JsonResponse(
        {
            "success": True,
            "commande": {
                "client_id": commande.client_id,
                "destination": commande.ville_arrivee,
                "produit_id": commande.produit_id,
                "quantite": str(commande.quantite or ""),
                "quantite_restante": str(
                    Decimal(commande.quantite or 0)
                    - Decimal(
                        commande.operations.filter(remplace_par__isnull=True).aggregate(total=Sum("quantite")).get("total")
                        or Decimal("0.00")
                    )
                ),
                "reference": commande.reference,
                "camion": commande.camion.numero_tracteur if commande.camion else "",
                "chauffeur": commande.chauffeur.nom if commande.chauffeur else "",
            },
        }
    )


def imprimer_bon_livraison(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related(
            "client",
            "commande",
            "camion",
            "camion__transporteur",
            "chauffeur",
            "produit",
            "regime_douanier",
            "depot",
        ),
        id=id,
    )
    if not operation.camion_id:
        return HttpResponse(
            "Le bon ne peut etre imprime qu'apres affectation du camion par la logistique.",
            status=400,
            content_type="text/plain; charset=utf-8",
        )

    try:
        import os
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader, simpleSplit
        from reportlab.pdfgen import canvas
    except ImportError:
        return HttpResponse(
            "Le module reportlab n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    def box(x, y, w, h, radius=8, fill_color=None, stroke_color=colors.HexColor("#73808c")):
        pdf.saveState()
        pdf.setStrokeColor(stroke_color)
        pdf.setLineWidth(1)
        if fill_color:
            pdf.setFillColor(fill_color)
            pdf.roundRect(x, y, w, h, radius, stroke=1, fill=1)
        else:
            pdf.roundRect(x, y, w, h, radius, stroke=1, fill=0)
        pdf.restoreState()

    def section_title(x, y, title):
        pdf.setFont("Helvetica-Bold", 8)
        pdf.setFillColor(colors.HexColor("#2f3d4a"))
        pdf.drawString(x, y, title)
        pdf.setFillColor(colors.black)

    def draw_lines(x, y, lines, max_width, line_gap=5.2 * mm, font_name="Helvetica", font_size=8.7, max_lines=4):
        wrapped_lines = []
        for line in lines:
            text = "" if line is None else str(line)
            wrapped_lines.extend(simpleSplit(text, font_name, font_size, max_width))
        if len(wrapped_lines) > max_lines:
            wrapped_lines = wrapped_lines[:max_lines]
            last = wrapped_lines[-1]
            if len(last) > 3:
                wrapped_lines[-1] = last[:-3].rstrip() + "..."
        pdf.setFont(font_name, font_size)
        current_y = y
        for line in wrapped_lines:
            pdf.drawString(x, current_y, line)
            current_y -= line_gap

    def format_quantite(value):
        if value in (None, ""):
            return "0"
        text = format(value, "f") if hasattr(value, "as_tuple") else str(value)
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if "." in text:
            entier, decimal = text.split(".", 1)
        else:
            entier, decimal = text, ""
        entier = entier[::-1]
        entier = ".".join(entier[i:i + 3] for i in range(0, len(entier), 3))[::-1]
        return f"{entier},{decimal}" if decimal else entier

    transporteur_nom = operation.camion.transporteur.nom if operation.camion.transporteur else "-"
    immatriculation = operation.camion.numero_tracteur
    if operation.camion.numero_citerne:
        immatriculation = f"{operation.camion.numero_tracteur} / {operation.camion.numero_citerne}"
    pdf.setTitle(f"BL {operation.numero_bl}")

    footer_text = (
        "Societe au capital de GNF: 1 000 000 000 sis au quartier Koulewondy , "
        "Commune de Kaloum-Conakry- Republique de Guinee BP 5420P\n"
        "Tel: (+224) 628 02 52 02 / 661 15 15 15 E-mail : patbeavoguigmail.com - "
        "No RCCM GN TCC 2020.B.10316 Code No NIF 173604760"
    )
    logo_path = r"C:\Users\HP\Downloads\Design-sans-titre-7.png"

    pdf.setFillColor(colors.HexColor("#c12f2f"))
    pdf.rect(0, 0, width, 16 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 8)
    footer_lines = footer_text.splitlines()
    pdf.drawCentredString(width / 2, 9 * mm, footer_lines[0])
    pdf.drawCentredString(width / 2, 4.2 * mm, footer_lines[1])

    if os.path.exists(logo_path):
        pdf.drawImage(ImageReader(logo_path), 12 * mm, height - 44 * mm, width=44 * mm, height=30 * mm, mask="auto")
    else:
        pdf.setFillColor(colors.HexColor("#0e4b78"))
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(14 * mm, height - 18 * mm, "SONI")
        pdf.setFillColor(colors.HexColor("#c12f2f"))
        pdf.drawString(28 * mm, height - 18 * mm, "ENERGY")
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 10)
    pdf.drawRightString(width - 16 * mm, height - 16 * mm, f"Conakry, le {timezone.localdate().strftime('%d/%m/%Y')}")
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawCentredString(width / 2, height - 30 * mm, f"BON DE LIVRAISON No  {operation.numero_bl}")

    if operation.remplace_par_id:
        pdf.setFillColor(colors.HexColor("#c12f2f"))
        pdf.setFont("Helvetica-Bold", 9)
        nouveau_camion = operation.remplace_par.camion.numero_tracteur if operation.remplace_par.camion else "-"
        pdf.drawCentredString(
            width / 2,
            height - 38 * mm,
            f"Ce BL n'est plus valide, car le camion a ete change par le {nouveau_camion}.",
        )
        pdf.setFillColor(colors.black)

    left_x = 16 * mm
    right_x = 108 * mm
    top_y = height - 74 * mm
    box_w = 84 * mm
    box_h = 29 * mm
    commande_ref = operation.commande.reference if operation.commande else "-"
    commande_header = f"REFERENCE DE COMMANDE : {commande_ref}"
    commande_header_lines = simpleSplit(commande_header, "Helvetica-Bold", 8, box_w - 10 * mm)
    commande_header = commande_header_lines[0] if commande_header_lines else "REFERENCE DE COMMANDE : -"
    if len(commande_header_lines) > 1:
        commande_header = commande_header.rstrip()
        while pdf.stringWidth(f"{commande_header}...", "Helvetica-Bold", 8) > (box_w - 10 * mm) and commande_header:
            commande_header = commande_header[:-1].rstrip()
        commande_header = f"{commande_header}..."

    box(left_x, top_y, box_w, box_h)
    box(right_x, top_y, box_w, box_h)
    box(left_x, top_y - 40 * mm, box_w, 38 * mm)
    box(right_x, top_y - 40 * mm, box_w, 38 * mm)

    section_title(left_x + 4 * mm, top_y + box_h - 6 * mm, "CLIENT")
    section_title(right_x + 4 * mm, top_y + box_h - 6 * mm, "LIVRAISON")
    section_title(left_x + 4 * mm, top_y - 12 * mm, commande_header)
    section_title(right_x + 4 * mm, top_y - 12 * mm, "INFOS TRANSPORTEUR")

    draw_lines(
        left_x + 4 * mm,
        top_y + box_h - 14 * mm,
        [
            operation.client.entreprise,
            operation.destination,
            f"{operation.client.ville} - Republique de Guinee",
        ],
        max_width=box_w - 8 * mm,
    )
    draw_lines(
        right_x + 4 * mm,
        top_y + box_h - 14 * mm,
        [
            operation.client.entreprise,
            operation.destination,
            f"{operation.client.ville} - Republique de Guinee",
        ],
        max_width=box_w - 8 * mm,
    )

    info_y = top_y - 19 * mm
    draw_lines(
        left_x + 4 * mm,
        info_y,
        [
            "Ref. Externe :",
            f"Regime douanier : {operation.regime_douanier.libelle if operation.regime_douanier else '-'}",
            f"Code Regime : {operation.regime_douanier.code_regime if operation.regime_douanier else '-'}",
            f"Lieu de livraison : {operation.destination or '-'}",
            f"Depot : {operation.depot.nom if operation.depot else '-'}",
        ],
        max_width=box_w - 8 * mm,
        line_gap=5.1 * mm,
        max_lines=5,
    )
    draw_lines(
        right_x + 4 * mm,
        info_y,
        [
            f"Transporteur : {transporteur_nom}",
            f"Immatriculation : {immatriculation}",
            f"Chauffeur : {operation.chauffeur.nom if operation.chauffeur else '-'}",
        ],
        max_width=box_w - 8 * mm,
        line_gap=7 * mm,
        max_lines=4,
    )

    table_y = top_y - 82 * mm
    box(left_x, table_y, 176 * mm, 23 * mm)
    pdf.setFillColor(colors.HexColor("#edf2f7"))
    pdf.rect(left_x, table_y + 14 * mm, 176 * mm, 9 * mm, stroke=0, fill=1)
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left_x + 2 * mm, table_y + 17 * mm, "Produits")
    pdf.drawString(left_x + 98 * mm, table_y + 17 * mm, "Unites")
    pdf.drawString(left_x + 132 * mm, table_y + 17 * mm, "Quantites")
    pdf.line(left_x, table_y + 14 * mm, left_x + 176 * mm, table_y + 14 * mm)
    pdf.line(left_x + 94 * mm, table_y, left_x + 94 * mm, table_y + 23 * mm)
    pdf.line(left_x + 126 * mm, table_y, left_x + 126 * mm, table_y + 23 * mm)
    pdf.setFont("Helvetica", 9)
    draw_lines(
        left_x + 2 * mm,
        table_y + 8 * mm,
        [operation.produit.nom if operation.produit else "-"],
        max_width=88 * mm,
        line_gap=4.2 * mm,
        font_size=8.2,
        max_lines=2,
    )
    pdf.drawString(left_x + 100 * mm, table_y + 6 * mm, "Litre")
    pdf.drawRightString(left_x + 173 * mm, table_y + 6 * mm, format_quantite(operation.quantite))
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(left_x + 2 * mm, table_y - 7 * mm, "Cumul")
    pdf.drawRightString(left_x + 173 * mm, table_y - 7 * mm, format_quantite(operation.quantite))

    sign_y = table_y - 57 * mm
    box(left_x, sign_y, 56 * mm, 37 * mm)
    box(left_x + 60 * mm, sign_y, 56 * mm, 37 * mm)
    box(left_x + 120 * mm, sign_y, 56 * mm, 37 * mm)
    box(left_x + 29 * mm, sign_y - 44 * mm, 118 * mm, 35 * mm)

    pdf.setFont("Helvetica-Bold", 6.7)
    pdf.drawCentredString(left_x + 28 * mm, sign_y + 29 * mm, "SIGNATURE ET CACHET DU DESTINATAIRE")
    pdf.drawCentredString(left_x + 88 * mm, sign_y + 29 * mm, "SIGNATURE ET CACHET SGP")
    pdf.drawCentredString(left_x + 148 * mm, sign_y + 29 * mm, "SIGNATURE ET CACHET DOUANE")
    pdf.drawCentredString(left_x + 88 * mm, sign_y - 13 * mm, "SIGNATURE AUTORISEE SONI ENERGY")

    pdf.showPage()
    pdf.save()

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="bon_livraison_{operation.numero_bl}.pdf"'
    response.write(buffer.getvalue())
    return response


liste_operations = role_required()(liste_operations)
export_operations_xls = role_required()(export_operations_xls)
export_operations_pdf = role_required()(export_operations_pdf)
ajouter_operation = role_required()(ajouter_operation)
modifier_operation = role_required()(modifier_operation)
supprimer_operation = role_required()(supprimer_operation)
comptable_operations = role_required("comptable")(comptable_operations)
secretaire_operations = role_required("secretaire")(secretaire_operations)
export_secretaire_xls = role_required("secretaire")(export_secretaire_xls)
export_secretaire_pdf = role_required("secretaire")(export_secretaire_pdf)
transmettre_bons_secretaire = role_required("secretaire")(transmettre_bons_secretaire)
sommiers_operations = role_required("comptable", "dga", "directeur")(sommiers_operations)
ajouter_operation_comptable = role_required("comptable")(ajouter_operation_comptable)
modifier_operation_comptable = role_required("comptable")(modifier_operation_comptable)
supprimer_operation_comptable = role_required("comptable")(supprimer_operation_comptable)
facturation_operations = role_required("comptable")(facturation_operations)
modifier_operation_facturation = role_required("comptable")(modifier_operation_facturation)
imprimer_facture_sans_tva = role_required("comptable")(imprimer_facture_sans_tva)
imprimer_facture_avec_tva = role_required("comptable")(imprimer_facture_avec_tva)
imprimer_facture_sans_tva_manquant = role_required("comptable")(imprimer_facture_sans_tva_manquant)
imprimer_facture_avec_tva_manquant = role_required("comptable")(imprimer_facture_avec_tva_manquant)
logistique_operations = role_required("logistique")(logistique_operations)
modifier_operation_logistique = role_required("logistique")(modifier_operation_logistique)
ancienne_fiche_operation_logistique = role_required("logistique")(ancienne_fiche_operation_logistique)
transitaire_operations = role_required("transitaire")(transitaire_operations)
export_transitaire_xls = role_required("transitaire")(export_transitaire_xls)
export_transitaire_pdf = role_required("transitaire")(export_transitaire_pdf)
export_transitaire_historique_xls = role_required("transitaire")(export_transitaire_historique_xls)
export_transitaire_historique_pdf = role_required("transitaire")(export_transitaire_historique_pdf)
historique_transitaire_operations = role_required("transitaire")(historique_transitaire_operations)
valider_reception_transitaire = role_required("transitaire")(valider_reception_transitaire)
changer_etat_transitaire = role_required("transitaire")(changer_etat_transitaire)
transferer_liquide_logistique = role_required("transitaire")(transferer_liquide_logistique)
charger_bon_direct = role_required("transitaire")(charger_bon_direct)
logisticien_operations = role_required("logistique")(logisticien_operations)
export_logisticien_xls = role_required("logistique")(export_logisticien_xls)
export_logisticien_pdf = role_required("logistique")(export_logisticien_pdf)
valider_receptions_logistiques = role_required("logistique")(valider_receptions_logistiques)
chef_chauffeur_operations = role_required("chef_chauffeur")(chef_chauffeur_operations)
historique_chef_chauffeur_operations = role_required("chef_chauffeur")(historique_chef_chauffeur_operations)
action_chef_chauffeur = role_required("chef_chauffeur")(action_chef_chauffeur)
action_groupee_transitaire = role_required("transitaire")(action_groupee_transitaire)
modifier_operation_logisticien = role_required("logistique", "chef_chauffeur")(modifier_operation_logisticien)
ajouter_produit_modal = role_required("comptable", "logistique")(ajouter_produit_modal)
ajouter_regime_modal = role_required("comptable", "logistique")(ajouter_regime_modal)
ajouter_depot_modal = role_required("comptable", "logistique")(ajouter_depot_modal)
chauffeur_par_camion = role_required("logistique")(chauffeur_par_camion)
commande_infos = role_required("comptable", "logistique")(commande_infos)
imprimer_bon_livraison = role_required("comptable")(imprimer_bon_livraison)
