from io import BytesIO
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Sum
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from camions.models import Camion
from commandes.models import Commande
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


def _facture_totals(operation, avec_tva=False):
    quantite = Decimal(operation.quantite or 0)
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


def _build_facture_pdf(operation, avec_tva=False):
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

    totals = _facture_totals(operation, avec_tva=avec_tva)
    numero_facture = operation.numero_facture or f"PROFORMA-{operation.numero_bl}"
    date_facture = operation.date_facture or timezone.localdate()
    logo_path = r"C:\Users\HP\Downloads\Design-sans-titre-7.png"
    footer_text = (
        "Societe au capital de GNF 50 000 000 sise au quartier Koulewondy, commune de Kaloum - Conakry - Republique de Guinee\n"
        "BP : 5420P / TEL : 00224 620 59 75 34 / 00224 661 15 15 15 Email: patbeavoguigmail.com / N RCCM : GN.TCC.2020.B.103"
    )

    def fmt(value, decimals=2):
        text = f"{Decimal(value):,.{decimals}f}"
        return text.replace(",", " ").replace(".", ",")

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
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(10 * mm, body_y - 6 * mm, f"{fmt(totals['montant_ttc' if avec_tva else 'montant_ht'], 0)} GNF")

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
        )
    if etat:
        operations = operations.filter(etat_bon=etat)
    if date_debut:
        operations = operations.filter(date_bl__gte=date_debut)
    if date_fin:
        operations = operations.filter(date_bl__lte=date_fin)

    return operations, query, etat, date_debut, date_fin


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

    commandes_pretes = Commande.objects.select_related("client", "produit", "camion", "chauffeur").filter(
        statut="planifiee"
    ).exclude(operations__isnull=False)
    operations = Operation.objects.select_related("commande", "client", "produit", "camion", "chauffeur", "remplace_par").prefetch_related("anciennes_versions")

    if query:
        commandes_pretes = commandes_pretes.filter(
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
    if commande_id:
        commande_initiale = get_object_or_404(
            Commande.objects.select_related("client", "produit", "camion", "chauffeur"),
            id=commande_id,
        )

    if request.method == "POST":
        form = ComptableOperationForm(request.POST)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.commande_id:
                operation.client = operation.commande.client
                operation.produit = operation.commande.produit
                operation.quantite = operation.commande.quantite
                operation.destination = operation.commande.ville_arrivee
                operation.camion = operation.commande.camion
                operation.chauffeur = operation.commande.chauffeur
            operation.save()
            return redirect("comptable_operations")
    else:
        initial = {}
        if commande_initiale:
            initial = {
                "commande": commande_initiale.id,
                "client": commande_initiale.client_id,
                "destination": commande_initiale.ville_arrivee,
                "produit": commande_initiale.produit_id,
                "quantite": commande_initiale.quantite,
            }
        form = ComptableOperationForm(initial=initial)

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
            "submit_label": "Enregistrer",
            "active_tab": "comptable",
            "commande_initiale": commande_initiale,
        },
    )


def modifier_operation_comptable(request, id):
    operation = get_object_or_404(Operation, id=id)
    if request.method == "POST":
        form = ComptableOperationForm(request.POST, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.commande_id:
                operation.client = operation.commande.client
                operation.produit = operation.commande.produit
                operation.quantite = operation.commande.quantite
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
            "submit_label": "Mettre a jour",
            "active_tab": "comptable",
        },
    )


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
    return _build_facture_pdf(operation, avec_tva=False)


def imprimer_facture_avec_tva(request, id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "commande", "produit", "camion", "chauffeur"),
        id=id,
        etat_bon="livre",
    )
    return _build_facture_pdf(operation, avec_tva=True)


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
    query = request.GET.get("q", "").strip()
    operations = Operation.objects.select_related("client", "camion", "chauffeur")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )

    return render(
        request,
        "operations/transitaire.html",
        {"operations": operations, "query": query, "active_tab": "transitaire"},
    )


def changer_etat_transitaire(request, id, etat):
    if request.method != "POST":
        return redirect("transitaire_operations")

    operation = get_object_or_404(Operation, id=id)
    today = timezone.localdate()

    if etat == "declare":
        operation.etat_bon = "declare"
        operation.date_transmission = today
    elif etat == "liquide":
        operation.etat_bon = "liquide"
        operation.date_bons_liquides = today
    try:
        operation.full_clean()
        if etat == "liquide":
            _decrement_sommier_stock_on_liquidation(operation)
        operation.save()
    except ValidationError as exc:
        if hasattr(exc, "message_dict"):
            for errors in exc.message_dict.values():
                for error in errors:
                    messages.error(request, error)
        else:
            for error in exc.messages:
                messages.error(request, error)

    return redirect("transitaire_operations")


def logisticien_operations(request):
    query = request.GET.get("q", "").strip()
    operations = Operation.objects.select_related("client", "camion", "chauffeur")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )

    return render(
        request,
        "operations/logisticien.html",
        {"operations": operations, "query": query, "active_tab": "logisticien"},
    )


def modifier_operation_logisticien(request, id):
    operation = get_object_or_404(Operation, id=id)
    if operation.remplace_par_id:
        messages.error(request, "Cet ancien BL n'est plus modifiable, car le camion a deja ete change.")
        return redirect("logisticien_operations")
    if request.method == "POST":
        form = LogisticienOperationForm(request.POST, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.etat_bon == "charge" and not operation.date_bons_charges:
                operation.date_bons_charges = timezone.localdate()
            if operation.etat_bon == "livre" and not operation.date_bons_livres:
                operation.date_bons_livres = timezone.localdate()
            operation.save()
            return redirect("logisticien_operations")
    else:
        form = LogisticienOperationForm(instance=operation)

    return render(
        request,
        "operations/logisticien_form.html",
        {
            "form": form,
            "operation": operation,
            "active_tab": "logisticien",
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
sommiers_operations = role_required("comptable", "dga", "directeur")(sommiers_operations)
ajouter_operation_comptable = role_required("comptable")(ajouter_operation_comptable)
modifier_operation_comptable = role_required("comptable")(modifier_operation_comptable)
facturation_operations = role_required("comptable")(facturation_operations)
modifier_operation_facturation = role_required("comptable")(modifier_operation_facturation)
imprimer_facture_sans_tva = role_required("comptable")(imprimer_facture_sans_tva)
imprimer_facture_avec_tva = role_required("comptable")(imprimer_facture_avec_tva)
logistique_operations = role_required("logistique")(logistique_operations)
modifier_operation_logistique = role_required("logistique")(modifier_operation_logistique)
ancienne_fiche_operation_logistique = role_required("logistique")(ancienne_fiche_operation_logistique)
transitaire_operations = role_required("transitaire")(transitaire_operations)
changer_etat_transitaire = role_required("transitaire")(changer_etat_transitaire)
logisticien_operations = role_required("logistique")(logisticien_operations)
modifier_operation_logisticien = role_required("logistique")(modifier_operation_logisticien)
ajouter_produit_modal = role_required("comptable", "logistique")(ajouter_produit_modal)
ajouter_regime_modal = role_required("logistique")(ajouter_regime_modal)
ajouter_depot_modal = role_required("logistique")(ajouter_depot_modal)
chauffeur_par_camion = role_required("logistique")(chauffeur_par_camion)
commande_infos = role_required("comptable", "logistique")(commande_infos)
imprimer_bon_livraison = role_required("comptable")(imprimer_bon_livraison)
