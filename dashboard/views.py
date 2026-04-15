from datetime import timedelta

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import render
from django.utils import timezone

from camions.models import Camion
from commandes.models import Commande
from maintenance.models import Maintenance
from operations.models import Operation
from utilisateurs.permissions import get_user_role, role_required


def dashboard(request):
    today = timezone.localdate()
    seuil_retard = today - timedelta(days=3)
    panne_threshold = 3
    user_role = get_user_role(request.user)
    is_maintenancier = user_role in {"maintenancier", "dga"}
    diagnostics_queryset = Maintenance.objects.exclude(statut__in=["refusee", "annulee"])

    camions_total = Camion.objects.count()
    camions_disponibles = Camion.objects.filter(etat="disponible").count()
    camions_mission = Camion.objects.filter(etat="mission").count()
    camions_maintenance = Camion.objects.filter(etat="au_garage").count()
    camions_vidange_due = Camion.objects.filter(
        kilometrage_alerte_vidange__isnull=False,
        kilometrage_actuel__gte=F("kilometrage_alerte_vidange"),
    ).count()
    maintenances_total = diagnostics_queryset.count()
    maintenances_en_cours = diagnostics_queryset.filter(statut="en_cours").count()
    maintenances_terminees = diagnostics_queryset.filter(statut="terminee").count()
    maintenances_refusees = Maintenance.objects.filter(statut="refusee").count()
    maintenances_annulees = Maintenance.objects.filter(statut="annulee").count()
    montant_maintenance_total = float(
        diagnostics_queryset.aggregate(total=Sum("total_facture"))["total"] or 0
    )
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
                filter=Q(maintenances__statut__in=["en_cours", "terminee"]),
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
        Q(statut="en_cours")
        | Q(
            camion__kilometrage_alerte_vidange__isnull=False,
            camion__kilometrage_actuel__gte=F("camion__kilometrage_alerte_vidange"),
        )
    ).exclude(statut__in=["refusee", "annulee"]).order_by("-date_creation")[:8]

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


dashboard = role_required(
    "commercial",
    "comptable",
    "logistique",
    "maintenancier",
    "dga",
    "directeur",
    "transitaire",
)(dashboard)
gps_monitor = role_required("commercial", "comptable", "logistique", "directeur", "transitaire")(gps_monitor)
