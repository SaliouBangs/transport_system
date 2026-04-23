from io import BytesIO
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from camions.models import Camion
from clients.models import Client
from operations.models import Operation
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, role_required

from .forms import CommandeAffectationForm, CommandeForm
from .models import Commande


def _commandes_queryset(request):
    user_role = get_user_role(request.user)
    query = request.GET.get("q", "").strip()
    statut = request.GET.get("statut", "").strip()
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()
    scope = request.GET.get("scope", "").strip() or "actives"

    commandes = Commande.objects.select_related("client", "produit").order_by("-date_creation")
    if user_role == "logistique":
        if scope == "historique":
            commandes = commandes.exclude(statut="validee_dg")
        else:
            commandes = commandes.filter(statut="validee_dg")
    else:
        if scope == "historique":
            commandes = commandes.exclude(statut="attente_validation_dg")
        else:
            commandes = commandes.filter(statut="attente_validation_dg")
    if query:
        commandes = commandes.filter(
            Q(reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(client__nom__icontains=query)
            | Q(produit__nom__icontains=query)
            | Q(ville_depart__icontains=query)
            | Q(ville_arrivee__icontains=query)
        )
    if statut:
        commandes = commandes.filter(statut=statut)
    if date_debut:
        commandes = commandes.filter(date_commande__gte=date_debut)
    if date_fin:
        commandes = commandes.filter(date_commande__lte=date_fin)

    return commandes, query, statut, date_debut, date_fin, scope, user_role


def liste_commandes(request):
    commandes, query, statut, date_debut, date_fin, scope, user_role = _commandes_queryset(request)
    return render(
        request,
        "commandes/commandes.html",
        {
            "commandes": commandes,
            "query": query,
            "statut": statut,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "scope": scope,
            "page_user_role": user_role,
            "statut_choices": Commande.STATUT_CHOICES,
            "current_filters": request.GET.urlencode(),
        },
    )


def ajouter_commande(request):
    if request.method == "POST":
        form = CommandeForm(request.POST)
        if form.is_valid():
            commande = form.save(commit=False)
            commande.statut = "attente_validation_dg"
            commande.save()
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


def valider_commande_dg(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.statut = "validee_dg"
    commande.date_validation_dg = timezone.localdate()
    commande.save(update_fields=["statut", "date_validation_dg"])
    journaliser_action(
        request.user,
        "Commandes",
        "Validation DG de commande",
        commande.reference,
        f"{request.user.username} a valide la commande {commande.reference} pour la logistique.",
    )
    messages.success(request, f"La commande {commande.reference} a ete validee par le DG.")
    return redirect("commandes")


def rejeter_commande_dg(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.statut = "rejetee_dg"
    commande.save(update_fields=["statut"])
    journaliser_action(
        request.user,
        "Commandes",
        "Rejet DG de commande",
        commande.reference,
        f"{request.user.username} a rejete la commande {commande.reference}.",
    )
    messages.error(request, f"La commande {commande.reference} a ete rejetee par le DG.")
    return redirect("/commandes/?scope=historique")


def apercu_commande_dg(request, id):
    commande = get_object_or_404(Commande.objects.select_related("client", "produit"), id=id)
    return render(
        request,
        "commandes/apercu_commande_dg.html",
        {
            "commande": commande,
        },
    )


def affecter_commande_logistique(request, id):
    commande = get_object_or_404(Commande.objects.select_related("client", "produit"), id=id)
    if commande.statut not in {"validee_dg", "planifiee"}:
        messages.error(request, "Cette commande doit d'abord etre validee par le DG.")
        return redirect("commandes")
    initial_data = {
        "camion": commande.camion_id,
        "chauffeur": commande.chauffeur_id,
    }

    if request.method == "POST":
        form = CommandeAffectationForm(request.POST)
        form.commande = commande
        if form.is_valid():
            camion = form.cleaned_data["camion"]
            chauffeur = form.cleaned_data["chauffeur"]
            raw_ids = request.POST.get("commandes_complementaires", "").strip()
            selected_ids = [int(item) for item in raw_ids.split(",") if item.strip().isdigit()]
            commandes_complementaires = list(
                Commande.objects.select_related("client", "produit")
                .filter(statut="validee_dg", camion__isnull=True, id__in=selected_ids)
                .exclude(id=commande.id)
                .order_by("date_commande", "reference")
            )
            if len(commandes_complementaires) != len(set(selected_ids)):
                form.add_error(None, "Certaines commandes ajoutees ne sont plus disponibles. Recharge la page et recommence.")
            else:
                total_quantite = Decimal(commande.quantite or 0) + sum(
                    Decimal(item.quantite or 0) for item in commandes_complementaires
                )
                capacite_camion = Decimal(camion.capacite or 0)
                if total_quantite != capacite_camion:
                    form.add_error(
                        "camion",
                        (
                            f"La somme des commandes ({total_quantite}) doit etre egale a la capacite du camion "
                            f"({capacite_camion}) avant validation de l'affectation."
                        ),
                    )
                else:
                    today = timezone.localdate()
                    commandes_a_affecter = [commande, *commandes_complementaires]
                    with transaction.atomic():
                        for item in commandes_a_affecter:
                            item.camion = camion
                            item.chauffeur = chauffeur
                            item.date_affectation_logistique = today
                            item.statut = "planifiee"
                            item.save(update_fields=["camion", "chauffeur", "date_affectation_logistique", "statut"])

                    references = ", ".join(item.reference for item in commandes_complementaires)
                    journaliser_action(
                        request.user,
                        "Commandes",
                        "Affectation logistique",
                        commande.reference,
                        (
                            f"{request.user.username} a affecte le camion "
                            f"{camion.numero_tracteur} aux commandes "
                            f"{commande.reference}"
                            f"{', ' + references if references else ''}."
                        ),
                    )
                    messages.success(
                        request,
                        (
                            f"Le camion {camion.numero_tracteur} a ete affecte a "
                            f"{1 + len(commandes_complementaires)} commande(s)."
                        ),
                    )
                    return redirect("commandes")
    else:
        form = CommandeAffectationForm(initial=initial_data)
        form.commande = commande

    commandes_candidates = list(
        Commande.objects.select_related("client", "produit")
        .filter(statut="validee_dg", camion__isnull=True)
        .exclude(id=commande.id)
        .order_by("date_commande", "reference")
    )
    commandes_candidates_data = [
        {
            "id": item.id,
            "reference": item.reference,
            "client": item.client.entreprise,
            "produit": item.produit.nom if item.produit else "",
            "quantite": float(item.quantite or 0),
            "trajet": f"{item.ville_depart} -> {item.ville_arrivee}",
        }
        for item in commandes_candidates
    ]

    return render(
        request,
        "commandes/affecter_commande.html",
        {
            "commande": commande,
            "form": form,
            "camions": form.fields["camion"].queryset.select_related("transporteur"),
            "commandes_candidates": commandes_candidates_data,
        },
    )


def completer_capacite_commande(request, id):
    messages.info(request, "Le complement de capacite se fait maintenant directement dans l'ecran d'affectation.")
    return redirect("affecter_commande_logistique", id=id)


def commande_camion_infos(request):
    camion_id = request.GET.get("camion_id")
    if not camion_id:
        return JsonResponse(
            {"success": False, "errors": {"camion": ["Camion manquant."]}},
            status=400,
        )

    camion = get_object_or_404(
        Camion.objects.select_related("transporteur").order_by("numero_tracteur"),
        id=camion_id,
    )
    chauffeur = camion.chauffeur_set.order_by("nom").first()
    recent_operations = (
        Operation.objects.filter(camion=camion)
        .select_related("commande")
        .order_by("-date_creation")[:5]
    )

    return JsonResponse(
        {
            "success": True,
            "camion": {
                "id": camion.id,
                "label": camion.numero_tracteur,
                "numero_tracteur": camion.numero_tracteur,
                "numero_citerne": camion.numero_citerne or "",
                "capacite": camion.capacite,
                "etat": camion.etat,
                "etat_label": camion.get_etat_display(),
                "transporteur": camion.transporteur.nom if camion.transporteur else "",
            },
            "chauffeur": {
                "id": chauffeur.id,
                "nom": chauffeur.nom,
            } if chauffeur else None,
            "operations": [
                {
                    "numero_bl": operation.numero_bl,
                    "client": operation.client.entreprise,
                    "destination": operation.destination,
                    "etat_bon": operation.get_etat_bon_display(),
                    "date_etat": (
                        operation.date_bons_livres.strftime("%Y-%m-%d")
                        if operation.etat_bon == "livre" and operation.date_bons_livres
                        else operation.date_bons_liquides.strftime("%Y-%m-%d")
                        if operation.etat_bon == "liquide" and operation.date_bons_liquides
                        else operation.date_bons_charges.strftime("%Y-%m-%d")
                        if operation.etat_bon == "charge" and operation.date_bons_charges
                        else operation.date_transmission.strftime("%Y-%m-%d")
                        if operation.etat_bon == "declare" and operation.date_transmission
                        else operation.date_bl.strftime("%Y-%m-%d")
                        if operation.date_bl
                        else ""
                    ),
                }
                for operation in recent_operations
            ],
        }
    )


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
    sheet.append(
        [
            "Reference",
            "Client",
            "Produit",
            "Quantite",
            "Prix negocie",
            "Depart",
            "Arrivee",
            "Livraison prevue",
            "Statut",
        ]
    )

    commandes, _, _, _, _, _, _ = _commandes_queryset(request)
    for commande in commandes:
        sheet.append(
            [
                commande.reference,
                commande.client.entreprise,
                commande.produit.nom if commande.produit else "",
                float(commande.quantite) if commande.quantite is not None else "",
                float(commande.prix_negocie) if commande.prix_negocie is not None else "",
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

    data = [[
        "Reference",
        "Client",
        "Produit",
        "Quantite",
        "Prix negocie",
        "Depart",
        "Arrivee",
        "Livraison",
        "Statut",
    ]]
    commandes, _, _, _, _, _, _ = _commandes_queryset(request)
    for commande in commandes:
        data.append(
            [
                commande.reference,
                commande.client.entreprise,
                commande.produit.nom if commande.produit else "",
                str(commande.quantite or ""),
                str(commande.prix_negocie or ""),
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


liste_commandes = role_required("commercial", "comptable", "directeur", "logistique")(liste_commandes)
ajouter_commande = role_required("commercial")(ajouter_commande)
modifier_commande = role_required("commercial")(modifier_commande)
supprimer_commande = role_required()(supprimer_commande)
valider_commande_dg = role_required("directeur")(valider_commande_dg)
rejeter_commande_dg = role_required("directeur")(rejeter_commande_dg)
apercu_commande_dg = role_required("directeur")(apercu_commande_dg)
affecter_commande_logistique = role_required("logistique")(affecter_commande_logistique)
completer_capacite_commande = role_required("logistique")(completer_capacite_commande)
commande_camion_infos = role_required("logistique")(commande_camion_infos)
export_commandes_xls = role_required("commercial", "comptable", "directeur", "logistique")(export_commandes_xls)
export_commandes_pdf = role_required("commercial", "comptable", "directeur", "logistique")(export_commandes_pdf)
