from io import BytesIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import LivraisonForm
from .models import Livraison


def liste_livraisons(request):
    livraisons = Livraison.objects.select_related(
        "commande",
        "commande__client",
        "camion",
        "chauffeur",
    )
    return render(request, "livraisons/livraisons.html", {"livraisons": livraisons})


def ajouter_livraison(request):
    if request.method == "POST":
        form = LivraisonForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("livraisons")
    else:
        form = LivraisonForm()

    return render(request, "livraisons/ajouter_livraison.html", {"form": form})


def modifier_livraison(request, id):
    livraison = get_object_or_404(Livraison, id=id)
    if request.method == "POST":
        form = LivraisonForm(request.POST, instance=livraison)
        if form.is_valid():
            form.save()
            return redirect("livraisons")
    else:
        form = LivraisonForm(instance=livraison)

    return render(
        request,
        "livraisons/modifier_livraison.html",
        {"form": form, "livraison": livraison},
    )


def supprimer_livraison(request, id):
    livraison = get_object_or_404(Livraison, id=id)
    livraison.delete()
    return redirect("livraisons")


def export_livraisons_xls(request):
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
    sheet.title = "Livraisons"
    sheet.append(["Commande", "Client", "Camion", "Chauffeur", "Depart", "Arrivee", "Statut"])

    livraisons = Livraison.objects.select_related(
        "commande",
        "commande__client",
        "camion",
        "chauffeur",
    ).order_by("-date_creation")
    for livraison in livraisons:
        sheet.append(
            [
                livraison.commande.reference,
                livraison.commande.client.entreprise,
                livraison.camion.immatriculation if livraison.camion else "",
                livraison.chauffeur.nom if livraison.chauffeur else "",
                livraison.date_depart.strftime("%Y-%m-%d"),
                livraison.date_arrivee.strftime("%Y-%m-%d") if livraison.date_arrivee else "",
                livraison.get_statut_display(),
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_livraisons.xlsx"'
    workbook.save(response)
    return response


def export_livraisons_pdf(request):
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

    data = [["Commande", "Client", "Camion", "Chauffeur", "Depart", "Arrivee", "Statut"]]
    livraisons = Livraison.objects.select_related(
        "commande",
        "commande__client",
        "camion",
        "chauffeur",
    ).order_by("-date_creation")
    for livraison in livraisons:
        data.append(
            [
                livraison.commande.reference,
                livraison.commande.client.entreprise,
                livraison.camion.immatriculation if livraison.camion else "",
                livraison.chauffeur.nom if livraison.chauffeur else "",
                livraison.date_depart.strftime("%Y-%m-%d"),
                livraison.date_arrivee.strftime("%Y-%m-%d") if livraison.date_arrivee else "",
                livraison.get_statut_display(),
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
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    doc.build([table])

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="rapport_livraisons.pdf"'
    response.write(buffer.getvalue())
    return response
