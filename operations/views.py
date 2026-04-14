from io import BytesIO

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from camions.models import Camion
from utilisateurs.permissions import role_required

from .forms import (
    ComptableOperationForm,
    DepotForm,
    FacturationOperationForm,
    LogistiqueOperationForm,
    LogisticienOperationForm,
    OperationForm,
    ProduitForm,
    RegimeDouanierForm,
)
from .models import Operation


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
    operations = Operation.objects.select_related("commande", "client", "produit")
    if query:
        operations = operations.filter(
            Q(numero_bl__icontains=query)
            | Q(commande__reference__icontains=query)
            | Q(client__entreprise__icontains=query)
        )

    return render(
        request,
        "operations/comptable.html",
        {"operations": operations, "query": query, "active_tab": "comptable"},
    )


def ajouter_operation_comptable(request):
    if request.method == "POST":
        form = ComptableOperationForm(request.POST)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.commande_id:
                operation.client = operation.commande.client
                operation.produit = operation.commande.produit
                operation.quantite = operation.commande.quantite
                operation.destination = operation.commande.ville_arrivee
            operation.save()
            return redirect("comptable_operations")
    else:
        form = ComptableOperationForm()

    return render(
        request,
        "operations/comptable_form.html",
        {
            "form": form,
            "regime_form": RegimeDouanierForm(),
            "depot_form": DepotForm(),
            "produit_form": ProduitForm(),
            "page_title": "Nouvelle fiche comptable",
            "submit_label": "Enregistrer",
            "active_tab": "comptable",
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
            "page_title": f"Comptable BL {operation.numero_bl}",
            "submit_label": "Mettre a jour",
            "active_tab": "comptable",
        },
    )


def facturation_operations(request):
    query = request.GET.get("q", "").strip()
    statut = request.GET.get("statut_facture", "").strip()
    operations = Operation.objects.select_related("commande", "client", "produit").filter(etat_bon="livre")
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
    if request.method == "POST":
        form = FacturationOperationForm(request.POST, instance=operation)
        if form.is_valid():
            operation = form.save(commit=False)
            if operation.numero_facture and not operation.date_facture:
                operation.date_facture = timezone.localdate()
            operation.save()
            return redirect("facturation_operations")
    else:
        form = FacturationOperationForm(instance=operation)

    return render(
        request,
        "operations/facturation_form.html",
        {
            "form": form,
            "operation": operation,
            "active_tab": "facturation",
        },
    )


def logistique_operations(request):
    query = request.GET.get("q", "").strip()
    scope = request.GET.get("scope", "").strip()
    operations = Operation.objects.select_related("client", "camion", "chauffeur")
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
            form.save()
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
        operation.save()
    except ValidationError as exc:
        for errors in exc.message_dict.values():
            for error in errors:
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

    commande = get_object_or_404(Commande.objects.select_related("client", "produit"), id=commande_id)
    return JsonResponse(
        {
            "success": True,
            "commande": {
                "client_id": commande.client_id,
                "destination": commande.ville_arrivee,
                "produit_id": commande.produit_id,
                "quantite": str(commande.quantite or ""),
                "reference": commande.reference,
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


liste_operations = role_required("directeur")(liste_operations)
export_operations_xls = role_required("directeur")(export_operations_xls)
export_operations_pdf = role_required("directeur")(export_operations_pdf)
ajouter_operation = role_required("directeur")(ajouter_operation)
modifier_operation = role_required("directeur")(modifier_operation)
supprimer_operation = role_required("directeur")(supprimer_operation)
comptable_operations = role_required("comptable", "directeur")(comptable_operations)
ajouter_operation_comptable = role_required("comptable", "directeur")(ajouter_operation_comptable)
modifier_operation_comptable = role_required("comptable", "directeur")(modifier_operation_comptable)
facturation_operations = role_required("comptable", "directeur")(facturation_operations)
modifier_operation_facturation = role_required("comptable", "directeur")(modifier_operation_facturation)
logistique_operations = role_required("logistique", "directeur")(logistique_operations)
modifier_operation_logistique = role_required("logistique", "directeur")(modifier_operation_logistique)
transitaire_operations = role_required("transitaire", "directeur")(transitaire_operations)
changer_etat_transitaire = role_required("transitaire", "directeur")(changer_etat_transitaire)
logisticien_operations = role_required("logistique", "directeur")(logisticien_operations)
modifier_operation_logisticien = role_required("logistique", "directeur")(modifier_operation_logisticien)
ajouter_produit_modal = role_required("comptable", "logistique", "directeur")(ajouter_produit_modal)
ajouter_regime_modal = role_required("logistique", "directeur")(ajouter_regime_modal)
ajouter_depot_modal = role_required("logistique", "directeur")(ajouter_depot_modal)
chauffeur_par_camion = role_required("logistique", "directeur")(chauffeur_par_camion)
commande_infos = role_required("comptable", "logistique", "directeur")(commande_infos)
imprimer_bon_livraison = role_required("comptable", "directeur")(imprimer_bon_livraison)
