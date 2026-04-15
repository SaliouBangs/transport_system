from io import BytesIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from clients.models import Client
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import role_required

from .forms import CommandeForm
from .models import Commande


def liste_commandes(request):
    commandes = Commande.objects.select_related("client")
    return render(request, "commandes/commandes.html", {"commandes": commandes})


def ajouter_commande(request):
    if request.method == "POST":
        form = CommandeForm(request.POST)
        if form.is_valid():
            commande = form.save()
            journaliser_action(
                request.user,
                "Commandes",
                "Ajout de commande",
                commande.reference,
                f"{request.user.username} a ajoute la commande {commande.reference}.",
            )
            return redirect("commandes")
    else:
        form = CommandeForm()

    return render(
        request,
        "commandes/ajouter_commande.html",
        {"form": form, "clients": Client.objects.order_by("entreprise", "nom")},
    )


def modifier_commande(request, id):
    commande = get_object_or_404(Commande, id=id)
    if request.method == "POST":
        form = CommandeForm(request.POST, instance=commande)
        if form.is_valid():
            commande = form.save()
            journaliser_action(
                request.user,
                "Commandes",
                "Modification de commande",
                commande.reference,
                f"{request.user.username} a modifie la commande {commande.reference}.",
            )
            return redirect("commandes")
    else:
        form = CommandeForm(instance=commande)

    return render(
        request,
        "commandes/modifier_commande.html",
        {
            "form": form,
            "commande": commande,
            "clients": Client.objects.order_by("entreprise", "nom"),
        },
    )


def supprimer_commande(request, id):
    commande = get_object_or_404(Commande, id=id)
    commande_label = commande.reference
    commande.delete()
    journaliser_action(
        request.user,
        "Commandes",
        "Suppression de commande",
        commande_label,
        f"{request.user.username} a supprime la commande {commande_label}.",
    )
    return redirect("commandes")


def export_commandes_xls(request):
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
    sheet.title = "Commandes"
    sheet.append(["Reference", "Client", "Produit", "Quantite", "Depart", "Arrivee", "Livraison prevue", "Statut"])

    commandes = Commande.objects.select_related("client").order_by("-date_creation")
    for commande in commandes:
        sheet.append(
            [
                commande.reference,
                commande.client.entreprise,
                commande.produit.nom if commande.produit else "",
                float(commande.quantite) if commande.quantite is not None else "",
                commande.ville_depart,
                commande.ville_arrivee,
                commande.date_livraison_prevue.strftime("%Y-%m-%d"),
                commande.get_statut_display(),
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_commandes.xlsx"'
    workbook.save(response)
    return response


def export_commandes_pdf(request):
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

    data = [["Reference", "Client", "Produit", "Quantite", "Depart", "Arrivee", "Livraison", "Statut"]]
    commandes = Commande.objects.select_related("client").order_by("-date_creation")
    for commande in commandes:
        data.append(
            [
                commande.reference,
                commande.client.entreprise,
                commande.produit.nom if commande.produit else "",
                str(commande.quantite or ""),
                commande.ville_depart,
                commande.ville_arrivee,
                commande.date_livraison_prevue.strftime("%Y-%m-%d"),
                commande.get_statut_display(),
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
    response["Content-Disposition"] = 'attachment; filename="rapport_commandes.pdf"'
    response.write(buffer.getvalue())
    return response


liste_commandes = role_required("commercial", "comptable", "directeur")(liste_commandes)
ajouter_commande = role_required("commercial", "directeur")(ajouter_commande)
modifier_commande = role_required("directeur")(modifier_commande)
supprimer_commande = role_required("directeur")(supprimer_commande)
export_commandes_xls = role_required("commercial", "comptable", "directeur")(export_commandes_xls)
export_commandes_pdf = role_required("commercial", "comptable", "directeur")(export_commandes_pdf)
