from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote_plus

from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from camions.models import Camion
from clients.models import Client
from clients.models import EncaissementClient
from commandes.models import Commande
from depenses.models import Depense
from maintenance.models import AlerteFactureResolue, ArticleStock, Maintenance, MouvementStock
from operations.models import Operation
from prospects.models import Prospect
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
    bons_secretaire = Operation.objects.filter(etat_bon="attente_reception_transitaire").count()
    bons_transmis = Operation.objects.filter(etat_bon="transmis").count()
    bons_declares = Operation.objects.filter(etat_bon="declare").count()
    bons_attente_reception_logistique = Operation.objects.filter(etat_bon="attente_reception_logistique").count()
    bons_charges = Operation.objects.filter(etat_bon="charge").count()
    bons_livres = Operation.objects.filter(etat_bon="livre").count()
    bons_liquides = Operation.objects.filter(etat_bon="liquide").count()
    bons_liquides_logistique = Operation.objects.filter(etat_bon="liquide_logistique").count()
    bons_liquides_chauffeur = Operation.objects.filter(etat_bon="liquide_chauffeur").count()
    bons_retournes = Operation.objects.filter(date_bon_retour__isnull=False).count()
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
    commandes_attente_dga = Commande.objects.filter(statut="attente_validation_dga").count()
    commandes_attente_dg = Commande.objects.filter(statut="attente_validation_dg").count()
    commandes_planifiees = Commande.objects.filter(statut="planifiee").count()
    depenses_attente_chargement_dga = Depense.objects.filter(
        statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
    ).count()
    depenses_attente_chargement_dg = Depense.objects.filter(
        statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
    ).count()
    depenses_attente_paiement = Depense.objects.filter(
        statut__in=[
            Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        ]
    ).count()
    commercial_clients_queryset = Client.objects.select_related("commercial").order_by("entreprise")
    commercial_prospects_queryset = Prospect.objects.select_related("commercial").order_by("entreprise")
    commercial_commandes_queryset = Commande.objects.select_related("client", "produit").order_by("-date_creation")
    if user_role == "commercial":
        commercial_clients_queryset = commercial_clients_queryset.filter(commercial=request.user)
        commercial_prospects_queryset = commercial_prospects_queryset.filter(commercial=request.user)
        commercial_commandes_queryset = commercial_commandes_queryset.filter(client__commercial=request.user)

    commercial_clients = list(commercial_clients_queryset)
    commercial_recent_commandes = list(commercial_commandes_queryset[:8])
    commercial_recent_prospects = list(commercial_prospects_queryset[:6])
    commercial_encaissements_queryset = EncaissementClient.objects.select_related("client", "commande").order_by(
        "-date_encaissement",
        "-id",
    )
    if user_role == "commercial":
        commercial_encaissements_queryset = commercial_encaissements_queryset.filter(client__commercial=request.user)
    commercial_prospects_total = commercial_prospects_queryset.count()
    commercial_clients_total = len(commercial_clients)
    commercial_commandes_total = commercial_commandes_queryset.count()
    commercial_commandes_ouvertes = commercial_commandes_queryset.exclude(
        statut__in=["rejetee_dg", "annulee", "livree"]
    ).count()
    commercial_commandes_attente = commercial_commandes_queryset.filter(
        statut__in=["attente_validation_dga", "attente_validation_dg"]
    ).count()
    commercial_commandes_numero_sage = commercial_commandes_queryset.filter(
        statut="validee_dg"
    ).filter(
        Q(reference__isnull=True) | Q(reference="")
    ).count()
    commercial_commandes_livrees = commercial_commandes_queryset.filter(statut="livree").count()
    commercial_montant_total = sum(
        float((commande.quantite or 0) * (commande.prix_negocie or 0))
        for commande in commercial_commandes_queryset
    )
    commercial_clients_dma_alerte = sum(
        1 for client in commercial_clients if float(client.ratio_decouvert or 0) >= 90
    )
    commercial_clients_focus = sorted(
        commercial_clients,
        key=lambda item: float(item.ratio_decouvert or 0),
        reverse=True,
    )[:6]
    commercial_recent_encaissements = list(commercial_encaissements_queryset[:6])
    commercial_encaissements_total = commercial_encaissements_queryset.aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    commercial_encours_total = sum((client.encours_client or Decimal("0.00")) for client in commercial_clients)
    commercial_creances_total = sum((client.creance_client or Decimal("0.00")) for client in commercial_clients)
    commercial_risque_total = sum((client.risque_client or Decimal("0.00")) for client in commercial_clients)
    commercial_clients_critique = sum(1 for client in commercial_clients if client.niveau_risque == "critique")
    commercial_clients_alerte = sum(1 for client in commercial_clients if client.niveau_risque == "alerte")
    commercial_clients_sans_plafond = sum(
        1 for client in commercial_clients if float(client.decouvert_maximum_autorise or 0) <= 0
    )
    commercial_commandes_validees = commercial_commandes_queryset.filter(
        statut__in=["validee_dg", "planifiee", "en_cours"]
    ).count()
    commercial_factures_emises = Operation.objects.exclude(
        Q(numero_facture__isnull=True) | Q(numero_facture="")
    )
    commercial_factures_a_emettre = Operation.objects.filter(etat_bon="livre").filter(
        Q(numero_facture__isnull=True) | Q(numero_facture="")
    )
    if user_role == "commercial":
        commercial_factures_emises = commercial_factures_emises.filter(client__commercial=request.user)
        commercial_factures_a_emettre = commercial_factures_a_emettre.filter(client__commercial=request.user)
    commercial_factures_emises_total = commercial_factures_emises.count()
    commercial_factures_a_emettre_total = commercial_factures_a_emettre.count()
    commercial_pipeline_labels = [
        "Attente DGA",
        "Attente DG",
        "Validees",
        "Ouvertes",
        "Livrees",
    ]
    commercial_pipeline_totals = [
        commercial_commandes_queryset.filter(statut="attente_validation_dga").count(),
        commercial_commandes_queryset.filter(statut="attente_validation_dg").count(),
        commercial_commandes_validees,
        commercial_commandes_ouvertes,
        commercial_commandes_livrees,
    ]
    comptable_commandes_pretes_queryset = (
        Commande.objects.select_related("client", "produit", "camion", "chauffeur")
        .filter(statut="planifiee")
        .exclude(operations__isnull=False)
        .order_by("-date_creation")
    )
    comptable_operations_initiees_queryset = (
        Operation.objects.select_related("commande", "client", "produit", "camion")
        .filter(etat_bon="initie")
        .order_by("-date_creation")
    )
    comptable_facturation_queryset = (
        Operation.objects.select_related("commande", "client", "produit", "camion", "chauffeur")
        .filter(etat_bon="livre")
        .order_by("-date_bons_livres", "-date_creation")
    )
    comptable_factures_a_emettre_queryset = comptable_facturation_queryset.filter(
        Q(numero_facture__isnull=True) | Q(numero_facture="")
    )
    comptable_factures_emises_queryset = comptable_facturation_queryset.exclude(
        Q(numero_facture__isnull=True) | Q(numero_facture="")
    )
    comptable_encaissements_queryset = EncaissementClient.objects.select_related("client", "commande").order_by(
        "-date_encaissement",
        "-id",
    )
    comptable_commandes_pretes_total = comptable_commandes_pretes_queryset.count()
    comptable_operations_initiees_total = comptable_operations_initiees_queryset.count()
    comptable_operations_livrees_total = comptable_facturation_queryset.count()
    comptable_factures_a_emettre_total = comptable_factures_a_emettre_queryset.count()
    comptable_factures_emises_total = comptable_factures_emises_queryset.count()
    comptable_montant_facture_total = comptable_factures_emises_queryset.aggregate(total=Sum("montant_facture")).get("total") or Decimal("0.00")
    comptable_encaissements_total = comptable_encaissements_queryset.aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    comptable_recent_commandes_pretes = list(comptable_commandes_pretes_queryset[:6])
    comptable_recent_operations_initiees = list(comptable_operations_initiees_queryset[:6])
    comptable_recent_facturation = list(comptable_facturation_queryset[:6])
    comptable_recent_encaissements = list(comptable_encaissements_queryset[:6])
    comptable_pipeline_labels = [
        "BL a creer",
        "BL inities",
        "BL livres",
        "A facturer",
        "Factures emises",
    ]
    comptable_pipeline_totals = [
        comptable_commandes_pretes_total,
        comptable_operations_initiees_total,
        comptable_operations_livrees_total,
        comptable_factures_a_emettre_total,
        comptable_factures_emises_total,
    ]

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
    transitaire_operations_dashboard = []
    chef_chauffeur_operations_dashboard = []
    logistique_operations_dashboard = []
    logistique_maintenance_dashboard = []
    logistique_commandes_a_affecter = 0
    logistique_prix_a_saisir = 0
    logistique_depenses_camion_en_cours = 0

    def add_alert(title, message, cta_label, cta_url, variant="warning", resolve_numero_facture=None):
        action_alerts.append(
            {
                "title": title,
                "message": message,
                "cta_label": cta_label,
                "cta_url": cta_url,
                "variant": variant,
                "resolve_numero_facture": resolve_numero_facture,
            }
        )
    if user_role == "logistique":
        commandes_a_affecter = Commande.objects.filter(statut="validee_dg").count()
        logistique_commandes_a_affecter = commandes_a_affecter
        if commandes_a_affecter:
            add_alert(
                "Commandes a affecter",
                f"{commandes_a_affecter} commande(s) validees par le DG attendent encore l'affectation d'un camion.",
                "Ouvrir les commandes",
                "/commandes/?statut=validee_dg",
                "danger",
            )
        receptions_logistique = Operation.objects.filter(etat_bon="attente_reception_logistique").count()
        if receptions_logistique:
            add_alert(
                "BL a receptionner",
                f"{receptions_logistique} BL liquide(s) attendent votre validation de reception logistique.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=attente_reception_logistique",
                "danger",
            )
        remises_chauffeur = Operation.objects.filter(etat_bon="liquide_logistique").count()
        if remises_chauffeur:
            add_alert(
                "BL a remettre au chauffeur",
                f"{remises_chauffeur} BL receptionne(s) attendent la remise chauffeur.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=liquide_logistique",
                "warning",
            )
        bl_charges = 0
        for operation in Operation.objects.filter(etat_bon="charge").prefetch_related("depenses_liees"):
            depenses_chargement = [
                depense
                for depense in operation.depenses_liees.all()
                if depense.source_depense == Depense.SOURCE_CHARGEMENT
            ]
            if not depenses_chargement or any(
                depense.statut in {
                    Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA,
                    Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG,
                }
                for depense in depenses_chargement
            ):
                bl_charges += 1
        if bl_charges:
            add_alert(
                "BL charges par le chauffeur",
                f"{bl_charges} BL charge(s) attendent encore la saisie ou la validation finale des depenses liees au chargement.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=charge",
                "ok",
            )
        bons_retour = Operation.objects.filter(etat_bon="livre", date_bon_retour__isnull=True).count()
        if bons_retour:
            add_alert(
                "Bons retour attendus",
                f"{bons_retour} BL livre(s) attendent encore le bon retour.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=livre",
                "warning",
            )
        depenses_chargement = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA).count()
        logistique_depenses_camion_en_cours = depenses_chargement
        if depenses_chargement:
            add_alert(
                "Depenses camion en cours",
                f"{depenses_chargement} bon(s) de depenses camion sont en attente de validation DGA.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_chargement_dga",
                "ok",
            )
        prix_a_saisir = Maintenance.objects.filter(statut="attente_prix").count()
        logistique_prix_a_saisir = prix_a_saisir
        if prix_a_saisir:
            add_alert(
                "Saisie des prix attendue",
                f"{prix_a_saisir} fiche(s) de maintenance attendent la saisie des prix.",
                "Ouvrir achat / prix",
                "/maintenance/achat/",
                "warning",
            )
        logistique_operations_dashboard = list(
            Operation.objects.select_related("client", "camion", "chauffeur", "commande", "produit")
            .filter(etat_bon__in=["attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"])
            .order_by("-date_creation")[:8]
        )
        logistique_maintenance_dashboard = list(
            Maintenance.objects.select_related("camion")
            .filter(statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg", "attente_paiement"])
            .order_by("-date_creation")[:6]
        )
    elif user_role == "chef_chauffeur":
        chargements = Operation.objects.filter(etat_bon="liquide_chauffeur").count()
        if chargements:
            add_alert(
                "BL a charger",
                f"{chargements} BL remis au chauffeur attendent le chargement.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=liquide_chauffeur",
                "danger",
            )
        livraisons = Operation.objects.filter(etat_bon="charge").count()
        if livraisons:
            add_alert(
                "BL a livrer",
                f"{livraisons} BL charge(s) attendent encore la livraison.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=charge",
                "warning",
            )
    elif user_role == "secretaire":
        bl_initie = Operation.objects.filter(etat_bon="initie").count()
        if bl_initie:
            add_alert(
                "BL a transmettre",
                f"{bl_initie} BL cree(s) par la comptabilite attendent la transmission au depot.",
                "Ouvrir secretaire BL",
                "/operations/secretaire/?etat=initie",
                "warning",
            )
    elif user_role in {"commercial", "responsable_commercial"}:
        if commercial_clients_dma_alerte:
            add_alert(
                "Clients en tension DMA",
                f"{commercial_clients_dma_alerte} client(s) du portefeuille sont proches ou depassent deja leur DMA.",
                "Ouvrir les clients",
                "/clients/",
                "danger",
            )
        if commercial_commandes_numero_sage:
            add_alert(
                "Numeros Sage a renseigner",
                f"{commercial_commandes_numero_sage} commande(s) validees par le DG attendent encore leur numero Sage.",
                "Ouvrir les commandes a numeroter",
                "/commandes/",
                "warning",
            )
    elif user_role == "dga":
        commandes_dga = Commande.objects.filter(statut="attente_validation_dga").count()
        if commandes_dga:
            add_alert(
                "Commandes a valider",
                f"{commandes_dga} commande(s) attendent votre decision DGA.",
                "Ouvrir les commandes",
                "/commandes/?statut=attente_validation_dga",
                "danger",
            )
        depenses_chargement_dga = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA).count()
        if depenses_chargement_dga:
            add_alert(
                "Depenses camion a valider",
                f"{depenses_chargement_dga} bon(s) de depenses camion attendent votre validation DGA.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_chargement_dga",
                "warning",
            )
        validations_dga = Maintenance.objects.filter(statut="attente_dga").count()
        if validations_dga:
            add_alert(
                "Validation DGA requise",
                f"{validations_dga} fiche(s) de maintenance attendent votre validation DGA.",
                "Ouvrir le garage",
                "/maintenance/garage/",
                "danger",
            )
    elif user_role == "caissiere":
        paiements_en_attente = Maintenance.objects.filter(statut="attente_paiement").count()
        if paiements_en_attente:
            add_alert(
                "Paiements a enregistrer",
                f"{paiements_en_attente} fiche(s) de maintenance attendent un paiement.",
                "Ouvrir les paiements",
                "/maintenance/paiements/",
                "ok",
            )
        depenses_espece = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE).count()
        if depenses_espece:
            add_alert(
                "Depenses a regler en espece",
                f"{depenses_espece} depense(s) attendent votre paiement en caisse.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_paiement_caissiere",
                "warning",
            )
    elif user_role == "comptable":
        commandes_a_transformer = Commande.objects.filter(statut="planifiee").exclude(operations__isnull=False).count()
        if commandes_a_transformer:
            add_alert(
                "BL a creer",
                f"{commandes_a_transformer} commande(s) validees attendent la creation du BL.",
                "Ouvrir operation comptable",
                "/operations/comptable/",
                "danger",
            )
        factures_a_traiter = Operation.objects.filter(etat_bon="livre").filter(
            Q(numero_facture__isnull=True) | Q(numero_facture="")
        ).count()
        if factures_a_traiter:
            add_alert(
                "Facturation en attente",
                f"{factures_a_traiter} bon(s) livres restent a facturer.",
                "Ouvrir la facturation",
                "/operations/facturation/?statut_facture=a_facturer",
                "warning",
            )
    elif user_role == "comptable_sogefi":
        depenses_cheque = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE).count()
        if depenses_cheque:
            add_alert(
                "Cheques a traiter",
                f"{depenses_cheque} depense(s) attendent le traitement comptable par cheque.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_paiement_comptable",
                "warning",
            )
    elif user_role == "responsable_achat":
        engagements = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_ENGAGEMENT).count()
        if engagements:
            add_alert(
                "Engagements a saisir",
                f"{engagements} expression(s) validees attendent votre engagement des depenses.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_engagement_achat",
                "warning",
            )
    elif user_role == "dga_sogefi":
        expressions = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION).count()
        engagements_dga = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DGA).count()
        if expressions:
            add_alert(
                "Expressions a valider",
                f"{expressions} expression(s) de besoin attendent votre decision.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_expression",
                "danger",
            )
        if engagements_dga:
            add_alert(
                "Engagements a valider",
                f"{engagements_dga} engagement(s) de depenses attendent votre validation DGA SOGEFI.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_dga_engagement",
                "warning",
            )
    elif user_role == "directeur":
        commandes_dg = Commande.objects.filter(statut="attente_validation_dg").count()
        if commandes_dg:
            add_alert(
                "Commandes a arbitrer",
                f"{commandes_dg} commande(s) attendent votre validation finale DG.",
                "Ouvrir les commandes",
                "/commandes/?statut=attente_validation_dg",
                "danger",
            )
        depenses_chargement_dg = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG).count()
        if depenses_chargement_dg:
            add_alert(
                "Depenses camion a arbitrer",
                f"{depenses_chargement_dg} bon(s) de depenses camion attendent votre validation DG.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_chargement_dg",
                "warning",
            )
        expressions = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION).count()
        engagements_dg = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DG).count()
        if expressions:
            add_alert(
                "Expressions en attente",
                f"{expressions} expression(s) de besoin peuvent etre traitees a votre niveau.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_expression",
                "danger",
            )
        if engagements_dg:
            add_alert(
                "Engagements a arbitrer",
                f"{engagements_dg} engagement(s) attendent votre validation finale et le choix du mode de paiement.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_dg_engagement",
                "warning",
            )
        validations_dg = Maintenance.objects.filter(statut="attente_dg").count()
        if validations_dg:
            add_alert(
                "Validation DG requise",
                f"{validations_dg} fiche(s) de maintenance attendent votre validation DG.",
                "Ouvrir le garage",
                "/maintenance/garage/",
                "danger",
            )
    elif user_role == "transitaire":
        bl_a_recevoir = Operation.objects.filter(etat_bon="attente_reception_transitaire").count()
        if bl_a_recevoir:
            add_alert(
                "BL a receptionner",
                f"{bl_a_recevoir} BL transmis par la secretaire attendent votre reception.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=attente_reception_transitaire",
                "danger",
            )
        bl_a_declarer = Operation.objects.filter(etat_bon="transmis").count()
        if bl_a_declarer:
            add_alert(
                "BL a declarer",
                f"{bl_a_declarer} BL recus attendent la declaration transitaire.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=transmis",
                "warning",
            )
        bl_a_liquider = Operation.objects.filter(etat_bon="declare").count()
        if bl_a_liquider:
            add_alert(
                "BL a liquider",
                f"{bl_a_liquider} BL declares attendent la liquidation.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=declare",
                "warning",
            )
        bl_liquides = Operation.objects.filter(etat_bon="liquide").count()
        if bl_liquides:
            add_alert(
                "BL liquides a orienter",
                f"{bl_liquides} BL liquides attendent soit un transfert logistique, soit un chargement direct.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=liquide",
                "ok",
            )
        transitaire_operations_dashboard = list(
            Operation.objects.select_related("client", "camion", "chauffeur", "commande")
            .filter(etat_bon__in=["attente_reception_transitaire", "transmis", "declare", "liquide"])
            .order_by("-date_creation")[:8]
        )
    elif user_role == "chef_chauffeur":
        bl_a_charger = Operation.objects.filter(etat_bon="liquide_chauffeur").count()
        if bl_a_charger:
            add_alert(
                "BL a charger",
                f"{bl_a_charger} BL attendent votre chargement.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=liquide_chauffeur",
                "danger",
            )
        bl_a_livrer = Operation.objects.filter(etat_bon="charge").count()
        if bl_a_livrer:
            add_alert(
                "BL a livrer",
                f"{bl_a_livrer} BL deja charges attendent maintenant la livraison.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=charge",
                "warning",
            )
        chef_chauffeur_operations_dashboard = list(
            Operation.objects.select_related("client", "camion", "chauffeur", "commande")
            .filter(etat_bon__in=["liquide_chauffeur", "charge"])
            .order_by("-date_creation")[:8]
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
    else:
        mes_depenses = Depense.objects.filter(
            demandeur=request.user,
            statut__in=[
                Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION,
                Depense.STATUT_ATTENTE_ENGAGEMENT,
                Depense.STATUT_ATTENTE_VALIDATION_DGA,
                Depense.STATUT_ATTENTE_VALIDATION_DG,
                Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
                Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            ],
        ).count()
        if mes_depenses:
            action_alerts.append(
                {
                    "title": "Suivi de vos depenses",
                    "message": f"{mes_depenses} expression(s) ou depense(s) que vous avez lancees sont encore en cours de traitement.",
                    "cta_label": "Ouvrir les depenses",
                    "cta_url": "/depenses/",
                    "variant": "ok",
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
        "bons_secretaire": bons_secretaire,
        "bons_transmis": bons_transmis,
        "bons_declares": bons_declares,
        "bons_attente_reception_logistique": bons_attente_reception_logistique,
        "bons_charges": bons_charges,
        "bons_livres": bons_livres,
        "bons_liquides": bons_liquides,
        "bons_liquides_logistique": bons_liquides_logistique,
        "bons_liquides_chauffeur": bons_liquides_chauffeur,
        "bons_retournes": bons_retournes,
        "bons_en_retard": bons_en_retard,
        "bons_non_retournes": bons_non_retournes,
        "montant_facture_total": montant_facture_total,
        "commandes_attente_dga": commandes_attente_dga,
        "commandes_attente_dg": commandes_attente_dg,
        "commandes_planifiees": commandes_planifiees,
        "depenses_attente_chargement_dga": depenses_attente_chargement_dga,
        "depenses_attente_chargement_dg": depenses_attente_chargement_dg,
        "depenses_attente_paiement": depenses_attente_paiement,
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
        "commercial_prospects_total": commercial_prospects_total,
        "commercial_clients_total": commercial_clients_total,
        "commercial_commandes_total": commercial_commandes_total,
        "commercial_commandes_ouvertes": commercial_commandes_ouvertes,
        "commercial_commandes_attente": commercial_commandes_attente,
        "commercial_commandes_numero_sage": commercial_commandes_numero_sage,
        "commercial_commandes_livrees": commercial_commandes_livrees,
        "commercial_montant_total": commercial_montant_total,
        "commercial_clients_dma_alerte": commercial_clients_dma_alerte,
        "commercial_clients_focus": commercial_clients_focus,
        "commercial_recent_commandes": commercial_recent_commandes,
        "commercial_recent_prospects": commercial_recent_prospects,
        "commercial_recent_encaissements": commercial_recent_encaissements,
        "commercial_encaissements_total": commercial_encaissements_total,
        "commercial_encours_total": commercial_encours_total,
        "commercial_creances_total": commercial_creances_total,
        "commercial_risque_total": commercial_risque_total,
        "commercial_clients_critique": commercial_clients_critique,
        "commercial_clients_alerte": commercial_clients_alerte,
        "commercial_clients_sans_plafond": commercial_clients_sans_plafond,
        "commercial_commandes_validees": commercial_commandes_validees,
        "commercial_factures_emises_total": commercial_factures_emises_total,
        "commercial_factures_a_emettre_total": commercial_factures_a_emettre_total,
        "commercial_pipeline_labels": commercial_pipeline_labels,
        "commercial_pipeline_totals": commercial_pipeline_totals,
        "comptable_commandes_pretes_total": comptable_commandes_pretes_total,
        "comptable_operations_initiees_total": comptable_operations_initiees_total,
        "comptable_operations_livrees_total": comptable_operations_livrees_total,
        "comptable_factures_a_emettre_total": comptable_factures_a_emettre_total,
        "comptable_factures_emises_total": comptable_factures_emises_total,
        "comptable_montant_facture_total": comptable_montant_facture_total,
        "comptable_encaissements_total": comptable_encaissements_total,
        "comptable_recent_commandes_pretes": comptable_recent_commandes_pretes,
        "comptable_recent_operations_initiees": comptable_recent_operations_initiees,
        "comptable_recent_facturation": comptable_recent_facturation,
        "comptable_recent_encaissements": comptable_recent_encaissements,
        "comptable_pipeline_labels": comptable_pipeline_labels,
        "comptable_pipeline_totals": comptable_pipeline_totals,
        "transitaire_operations_dashboard": transitaire_operations_dashboard,
        "chef_chauffeur_operations_dashboard": chef_chauffeur_operations_dashboard,
        "logistique_operations_dashboard": logistique_operations_dashboard,
        "logistique_maintenance_dashboard": logistique_maintenance_dashboard,
        "logistique_commandes_a_affecter": logistique_commandes_a_affecter,
        "logistique_prix_a_saisir": logistique_prix_a_saisir,
        "logistique_depenses_camion_en_cours": logistique_depenses_camion_en_cours,
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
    "dga_sogefi",
    "directeur",
    "responsable_achat",
    "comptable_sogefi",
    "transitaire",
    "controleur",
    "secretaire",
    "chef_chauffeur",
)(dashboard)
gps_monitor = role_required("commercial", "comptable", "logistique", "transitaire")(gps_monitor)
resoudre_alerte_facture = role_required("controleur")(resoudre_alerte_facture)
