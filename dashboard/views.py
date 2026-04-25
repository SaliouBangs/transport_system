from datetime import timedelta
from urllib.parse import quote_plus

from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from camions.models import Camion
from commandes.models import Commande
from maintenance.models import AlerteFactureResolue, ArticleStock, Maintenance, MouvementStock
from operations.models import Operation
from utilisateurs.permissions import get_user_role, role_required


def dashboard(request):
    today = timezone.localdate()
    seuil_retard = today - timedelta(days=3)
    panne_threshold = 3
    user_role = get_user_role(request.user)
    is_maintenancier = user_role in {"maintenancier", "dga"}
    diagnostics_queryset = Maintenance.objects.exclude(statut__in=["rejetee_dga", "rejetee_dg"])

    camions_total = Camion.objects.count()
    camions_disponibles = Camion.objects.filter(etat="disponible").count()
    camions_mission = Camion.objects.filter(etat="mission").count()
    camions_maintenance = Camion.objects.filter(etat="au_garage").count()
    camions_vidange_due = Camion.objects.filter(
        kilometrage_alerte_vidange__isnull=False,
        kilometrage_actuel__gte=F("kilometrage_alerte_vidange"),
    ).count()
    maintenances_total = diagnostics_queryset.count()
    maintenances_en_cours = diagnostics_queryset.filter(statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg"]).count()
    maintenances_terminees = diagnostics_queryset.filter(statut__in=["attente_paiement", "payee"]).count()
    maintenances_refusees = Maintenance.objects.filter(statut="rejetee_dga").count()
    maintenances_annulees = Maintenance.objects.filter(statut="rejetee_dg").count()
    montant_maintenance_total = float(
        diagnostics_queryset.aggregate(total=Sum("total_facture"))["total"] or 0
    )
    stock_total_articles = ArticleStock.objects.count()
    stock_articles_alerte = ArticleStock.objects.filter(
        seuil_alerte__gt=0,
        quantite_stock__lte=F("seuil_alerte"),
    ).count()
    stock_quantite_totale = float(
        ArticleStock.objects.aggregate(total=Sum("quantite_stock"))["total"] or 0
    )
    stock_mouvements_recents = MouvementStock.objects.count()
    stock_resume = ArticleStock.objects.filter(
        Q(seuil_alerte__gt=0, quantite_stock__lte=F("seuil_alerte")) | Q(quantite_stock__gt=0)
    ).order_by(
        "quantite_stock",
        "libelle",
    )[:6]
    for article in stock_resume:
        article.stock_equivalent_display = (
            article.get_quantite_decomposee() if article.conversions.exists() else ""
        )
    stock_chart_items = list(
        ArticleStock.objects.filter(quantite_stock__gt=0)
        .order_by("-quantite_stock", "libelle")[:5]
    )
    stock_chart_labels = [item.libelle for item in stock_chart_items]
    stock_chart_quantities = [float(item.quantite_stock or 0) for item in stock_chart_items]
    stock_chart_colors = [
        "#d64545" if item.en_alerte else color
        for item, color in zip(
            stock_chart_items,
            ["#1f9d7a", "#3f8fd6", "#f0a83a", "#123047", "#4fb3bf"],
        )
    ]
    commandes_total = Commande.objects.count()
    operations_total = Operation.objects.count()

    bons_inities = Operation.objects.filter(etat_bon="initie").count()
    bons_declares = Operation.objects.filter(etat_bon="declare").count()
    bons_charges = Operation.objects.filter(etat_bon="charge").count()
    bons_livres = Operation.objects.filter(etat_bon="livre").count()
    bons_liquides = Operation.objects.filter(etat_bon="liquide").count()
    bons_en_retard = Operation.objects.filter(
        date_bons_charges__isnull=False,
        date_bons_livres__isnull=True,
        date_bons_charges__lt=seuil_retard,
    ).count()
    bons_non_retournes = Operation.objects.filter(
        date_bons_livres__isnull=False,
        date_bon_retour__isnull=True,
    ).count()
    montant_facture_total = float(
        Operation.objects.aggregate(total=Sum("montant_facture"))["total"] or 0
    )

    dernieres_operations = Operation.objects.select_related(
        "client",
        "camion",
        "chauffeur",
        "produit",
    ).order_by("-date_creation")[:8]
    alertes_operations = Operation.objects.select_related("client").filter(
        Q(date_bons_charges__isnull=False, date_bons_livres__isnull=True, date_bons_charges__lt=seuil_retard)
        | Q(date_bons_livres__isnull=False, date_bon_retour__isnull=True)
    ).order_by("-date_creation")[:6]

    top_clients = (
        Operation.objects.values("client__entreprise")
        .annotate(total_bons=Count("id"), total_quantite=Sum("quantite"))
        .order_by("-total_bons", "-total_quantite")[:5]
    )
    camions_plus_utilises = (
        Camion.objects.filter(operations__isnull=False)
        .values("numero_tracteur", "numero_citerne", "chauffeur__nom")
        .annotate(total_bons=Count("operations", distinct=True))
        .order_by("-total_bons")[:5]
    )

    quantites_carburant = Operation.objects.aggregate(
        total_essence=Sum("quantite", filter=Q(produit__nom__icontains="essence")),
        total_gasoil=Sum("quantite", filter=Q(produit__nom__icontains="gasoil")),
    )
    total_essence = float(quantites_carburant["total_essence"] or 0)
    total_gasoil = float(quantites_carburant["total_gasoil"] or 0)

    daily_inities = (
        Operation.objects.filter(etat_bon="initie")
        .annotate(day=TruncDate("date_creation"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    daily_labels = [item["day"].strftime("%d/%m/%Y") for item in daily_inities if item["day"]]
    daily_totals = [item["total"] for item in daily_inities if item["day"]]

    performances_camions = list(
        Camion.objects.annotate(
            panne_count=Count(
                "maintenances",
                filter=Q(maintenances__statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg", "attente_paiement", "payee"]),
                distinct=True,
            )
        ).order_by("numero_tracteur")
    )
    for camion in performances_camions:
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

    dernieres_maintenances = diagnostics_queryset.select_related("camion").order_by("-date_creation")[:8]
    alertes_maintenance = Maintenance.objects.select_related("camion").filter(
        Q(statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg"])
        | Q(
            camion__kilometrage_alerte_vidange__isnull=False,
            camion__kilometrage_actuel__gte=F("camion__kilometrage_alerte_vidange"),
        )
    ).exclude(statut__in=["rejetee_dga", "rejetee_dg"]).order_by("-date_creation")[:8]

    action_alerts = []
    if user_role == "logistique":
        prix_a_saisir = Maintenance.objects.filter(statut="attente_prix").count()
        if prix_a_saisir:
            action_alerts.append(
                {
                    "title": "Saisie des prix attendue",
                    "message": f"{prix_a_saisir} fiche(s) de maintenance attendent la saisie des prix.",
                    "cta_label": "Ouvrir achat / prix",
                    "cta_url": "/maintenance/achat/",
                    "variant": "warning",
                }
            )
    elif user_role == "dga":
        validations_dga = Maintenance.objects.filter(statut="attente_dga").count()
        if validations_dga:
            action_alerts.append(
                {
                    "title": "Validation DGA requise",
                    "message": f"{validations_dga} fiche(s) de maintenance attendent votre validation DGA.",
                    "cta_label": "Ouvrir le garage",
                    "cta_url": "/maintenance/garage/",
                    "variant": "danger",
                }
            )
    elif user_role == "directeur":
        validations_dg = Maintenance.objects.filter(statut="attente_dg").count()
        if validations_dg:
            action_alerts.append(
                {
                    "title": "Validation DG requise",
                    "message": f"{validations_dg} fiche(s) de maintenance attendent votre validation DG.",
                    "cta_label": "Ouvrir le garage",
                    "cta_url": "/maintenance/garage/",
                    "variant": "danger",
                }
            )
    elif user_role == "caissiere":
        paiements_en_attente = Maintenance.objects.filter(statut="attente_paiement").count()
        if paiements_en_attente:
            action_alerts.append(
                {
                    "title": "Paiements a enregistrer",
                    "message": f"{paiements_en_attente} fiche(s) de maintenance attendent un paiement.",
                    "cta_label": "Ouvrir les paiements",
                    "cta_url": "/maintenance/paiements/",
                    "variant": "ok",
                }
            )
    elif user_role == "comptable":
        factures_a_traiter = Operation.objects.filter(etat_bon="livre").filter(
            Q(numero_facture__isnull=True) | Q(numero_facture="")
        ).count()
        if factures_a_traiter:
            action_alerts.append(
                {
                    "title": "Facturation en attente",
                    "message": f"{factures_a_traiter} bon(s) livres restent a facturer.",
                    "cta_label": "Ouvrir la facturation",
                    "cta_url": "/operations/facturation/",
                    "variant": "warning",
                }
            )
    elif user_role == "transitaire":
        transits_a_traiter = Operation.objects.filter(etat_bon__in=["initie", "declare"]).count()
        if transits_a_traiter:
            action_alerts.append(
                {
                    "title": "Etat des bons a mettre a jour",
                    "message": f"{transits_a_traiter} bon(s) attendent une action transitaire.",
                    "cta_label": "Ouvrir le transitaire",
                    "cta_url": "/operations/transitaire/",
                    "variant": "warning",
                }
            )
    elif user_role == "invite":
        action_alerts.append(
            {
                "title": "Mode lecture seule",
                "message": "Ce compte invite permet uniquement de consulter le dashboard, la maintenance, les camions et les chauffeurs.",
                "cta_label": "Voir la maintenance",
                "cta_url": "/maintenance/",
                "variant": "ok",
            }
        )
    elif user_role == "controleur":
        resolved_factures = set(
            AlerteFactureResolue.objects.values_list("numero_facture", flat=True)
        )
        doublons_factures = list(
            Maintenance.objects.exclude(numero_facture="")
            .values("numero_facture")
            .annotate(total=Count("id"))
            .filter(total__gt=1)
            .order_by("-total", "numero_facture")[:3]
        )
        doublons_factures = [
            item for item in doublons_factures
            if item["numero_facture"] not in resolved_factures
        ]
        if doublons_factures:
            premier = doublons_factures[0]
            action_alerts.append(
                {
                    "title": "Doublons de factures detectes",
                    "message": f"Le numero {premier['numero_facture']} existe deja plusieurs fois dans la base. Merci de controler les fiches achat / prix.",
                    "cta_label": "Ouvrir achat / prix",
                    "cta_url": f"/maintenance/achat/?scope=historique&q={quote_plus(premier['numero_facture'])}",
                    "variant": "danger",
                    "resolve_numero_facture": premier["numero_facture"],
                }
            )

    context = {
        "user_role": user_role,
        "is_maintenancier": is_maintenancier,
        "camions_total": camions_total,
        "camions_disponibles": camions_disponibles,
        "camions_mission": camions_mission,
        "camions_maintenance": camions_maintenance,
        "camions_vidange_due": camions_vidange_due,
        "maintenances_total": maintenances_total,
        "maintenances_en_cours": maintenances_en_cours,
        "maintenances_terminees": maintenances_terminees,
        "maintenances_refusees": maintenances_refusees,
        "maintenances_annulees": maintenances_annulees,
        "montant_maintenance_total": montant_maintenance_total,
        "stock_total_articles": stock_total_articles,
        "stock_articles_alerte": stock_articles_alerte,
        "stock_quantite_totale": stock_quantite_totale,
        "stock_mouvements_recents": stock_mouvements_recents,
        "stock_resume": stock_resume,
        "stock_chart_labels": stock_chart_labels,
        "stock_chart_quantities": stock_chart_quantities,
        "stock_chart_colors": stock_chart_colors,
        "dernieres_maintenances": dernieres_maintenances,
        "alertes_maintenance": alertes_maintenance,
        "commandes_total": commandes_total,
        "operations_total": operations_total,
        "bons_inities": bons_inities,
        "bons_declares": bons_declares,
        "bons_charges": bons_charges,
        "bons_livres": bons_livres,
        "bons_liquides": bons_liquides,
        "bons_en_retard": bons_en_retard,
        "bons_non_retournes": bons_non_retournes,
        "montant_facture_total": montant_facture_total,
        "dernieres_operations": dernieres_operations,
        "alertes_operations": alertes_operations,
        "top_clients": top_clients,
        "camions_plus_utilises": camions_plus_utilises,
        "total_essence": total_essence,
        "total_gasoil": total_gasoil,
        "daily_labels": daily_labels,
        "daily_totals": daily_totals,
        "performances_camions": performances_camions,
        "seuil_retard_jours": 3,
        "action_alerts": action_alerts,
    }

    return render(request, "dashboard/dashboard.html", context)


def gps_monitor(request):
    return render(
        request,
        "dashboard/gps_monitor.html",
        {
            "gps_url": "https://www.gps51.com/#/monitorPage",
        },
    )


@require_POST
def resoudre_alerte_facture(request):
    numero_facture = (request.POST.get("numero_facture") or "").strip()
    if not numero_facture:
        messages.error(request, "Numero de facture manquant pour la resolution de l'alerte.")
        return redirect("/dashboard/")

    AlerteFactureResolue.objects.get_or_create(
        numero_facture=numero_facture,
        defaults={"resolved_by": request.user},
    )
    messages.success(request, f"L'alerte sur la facture {numero_facture} a ete marquee comme resolue.")
    return redirect("/dashboard/")


dashboard = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "caissiere",
    "invite",
    "logistique",
    "maintenancier",
    "dga",
    "directeur",
    "transitaire",
    "controleur",
)(dashboard)
gps_monitor = role_required("commercial", "comptable", "logistique", "transitaire")(gps_monitor)
resoudre_alerte_facture = role_required("controleur")(resoudre_alerte_facture)
