from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote_plus

from django.contrib import messages
from django.contrib.auth.models import User
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
from maintenance.models import AlerteFactureResolue, ApprovisionnementCaisse, ArticleStock, Maintenance, MouvementStock, SoldeInitialCaisse
from operations.models import DemandeNouveauBL, Operation
from prospects.models import Prospect
from utilisateurs.permissions import get_user_role, role_required


def _dashboard_caisse_scope_user_ids(request):
    user_role = get_user_role(request.user)
    if user_role in {"caissiere", "caissiere_soni", "caissiere_avena"}:
        return [request.user.id]
    if user_role == "comptable":
        return list(
            User.objects.filter(groups__name="caissiere_soni", is_active=True)
            .values_list("id", flat=True)
            .distinct()
        )
    if user_role == "comptable_avena":
        return list(
            User.objects.filter(groups__name="caissiere_avena", is_active=True)
            .values_list("id", flat=True)
            .distinct()
        )
    if user_role == "comptable_sogefi":
        return list(
            User.objects.filter(groups__name="caissiere", is_active=True)
            .values_list("id", flat=True)
            .distinct()
        )
    return list(
        User.objects.filter(groups__name__in=["caissiere", "caissiere_soni", "caissiere_avena"], is_active=True)
        .values_list("id", flat=True)
        .distinct()
    )


def _dashboard_internal_entity_scope(user_role):
    if user_role in {"dga_sogefi", "responsable_achat", "comptable_sogefi", "caissiere"}:
        return Depense.ENTITE_SOGEFI
    if user_role in {"logistique", "dga", "comptable", "caissiere_soni"}:
        return Depense.ENTITE_SONI
    if user_role in {"dga_avena", "comptable_avena", "caissiere_avena"}:
        return Depense.ENTITE_AVENA
    return ""


def _dashboard_depenses_chargement_queryset_for_operation(operation):
    base_queryset = Depense.objects.filter(source_depense=Depense.SOURCE_CHARGEMENT)
    if operation.commande_id:
        return base_queryset.filter(
            Q(operation_id=operation.id, portee_chargement=Depense.PORTEE_BL)
            | Q(commande_id=operation.commande_id, portee_chargement=Depense.PORTEE_COMMANDE)
        ).distinct()
    return base_queryset.filter(operation_id=operation.id)


def _dashboard_depense_chargement_stage(operation):
    depenses_chargement = list(_dashboard_depenses_chargement_queryset_for_operation(operation))
    if not depenses_chargement:
        return "logistique"
    if any(depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA for depense in depenses_chargement):
        return "dga"
    if any(depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG for depense in depenses_chargement):
        return "dg"
    if any(
        depense.statut
        in {
            Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        }
        for depense in depenses_chargement
    ):
        return "paiement"
    if any(depense.statut == Depense.STATUT_PAYEE for depense in depenses_chargement):
        return "payee"
    return "paiement"


def _dashboard_count_charged_bl_with_pending_depenses(operations):
    return sum(
        1
        for operation in operations
        if _dashboard_depense_chargement_stage(operation) in {"logistique", "dga", "dg", "paiement"}
    )


def dashboard(request):
    today = timezone.localdate()
    seuil_retard = today - timedelta(days=3)
    panne_threshold = 3
    user_role = get_user_role(request.user)
    is_maintenancier = user_role in {"maintenancier", "dga"}
    diagnostics_queryset = Maintenance.objects.exclude(statut__in=["rejetee_dga", "rejetee_dg"])
    active_operations = Operation.objects.filter(remplace_par__isnull=True)

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
    operations_total = active_operations.count()

    bons_inities = active_operations.filter(etat_bon="initie").count()
    bons_secretaire = active_operations.filter(etat_bon="attente_reception_transitaire").count()
    bons_transmis = active_operations.filter(etat_bon="transmis").count()
    bons_declares = active_operations.filter(etat_bon="declare").count()
    bons_attente_reception_logistique = active_operations.filter(etat_bon="attente_reception_logistique").count()
    bons_charges = active_operations.filter(etat_bon="charge").count()
    bons_livres = active_operations.filter(etat_bon="livre").count()
    bons_liquides = active_operations.filter(etat_bon="liquide").count()
    bons_liquides_logistique = active_operations.filter(etat_bon="liquide_logistique").count()
    bons_liquides_chauffeur = active_operations.filter(etat_bon="liquide_chauffeur").count()
    bons_retournes = active_operations.filter(date_bon_retour__isnull=False).count()
    bons_en_retard = active_operations.filter(
        date_bons_charges__isnull=False,
        date_bons_livres__isnull=True,
        date_bons_charges__lt=seuil_retard,
    ).count()
    bons_non_retournes = active_operations.filter(
        date_bons_livres__isnull=False,
        date_bon_retour__isnull=True,
    ).count()
    montant_facture_total = float(
        active_operations.aggregate(total=Sum("montant_facture"))["total"] or 0
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
    depenses_attente_cheque_queryset = Depense.objects.filter(
        statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE
    )
    internal_entity_scope = _dashboard_internal_entity_scope(user_role)
    if internal_entity_scope:
        depenses_attente_cheque_queryset = depenses_attente_cheque_queryset.filter(
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=internal_entity_scope,
        )
    depenses_attente_cheque = depenses_attente_cheque_queryset.count()
    depenses_attente_paiement = Depense.objects.filter(
        statut__in=[
            Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            ]
        ).count()
    depenses_internes_queryset = Depense.objects.filter(source_depense=Depense.SOURCE_GENERALE).select_related(
        "demandeur",
        "fournisseur",
        "validation_dga_par",
        "validation_dg_par",
    )
    if internal_entity_scope:
        depenses_internes_queryset = depenses_internes_queryset.filter(entite_depense=internal_entity_scope)
    depenses_internes_total = depenses_internes_queryset.count()
    depenses_internes_attente_achat = depenses_internes_queryset.filter(
        statut=Depense.STATUT_ATTENTE_ENGAGEMENT
    ).count()
    depenses_internes_attente_dga = depenses_internes_queryset.filter(
        statut=Depense.STATUT_ATTENTE_VALIDATION_DGA
    ).count()
    depenses_internes_attente_dg = depenses_internes_queryset.filter(
        statut=Depense.STATUT_ATTENTE_VALIDATION_DG
    ).count()
    depenses_internes_attente_paiement = depenses_internes_queryset.filter(
        statut__in=[
            Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        ]
    ).count()
    depenses_internes_attente_caisse = depenses_internes_queryset.filter(
        statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE
    ).count()
    depenses_internes_attente_comptable = depenses_internes_queryset.filter(
        statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE
    ).count()
    depenses_internes_payees = depenses_internes_queryset.filter(
        statut=Depense.STATUT_PAYEE
    ).count()
    depenses_internes_montant_total = (
        depenses_internes_queryset.aggregate(total=Sum("montant_engage")).get("total")
        or Decimal("0.00")
    )
    depenses_internes_montant_en_attente = (
        depenses_internes_queryset.filter(
            statut__in=[
                Depense.STATUT_ATTENTE_VALIDATION_DGA,
                Depense.STATUT_ATTENTE_VALIDATION_DG,
                Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            ]
        ).aggregate(total=Sum("montant_engage")).get("total")
        or Decimal("0.00")
    )
    depenses_internes_recentes = list(
        depenses_internes_queryset.order_by("-date_creation", "-id")[:6]
    )
    caissiere_user_ids = _dashboard_caisse_scope_user_ids(request)
    caisse_solde_initial_total = SoldeInitialCaisse.objects.filter(caissiere_id__in=caissiere_user_ids).aggregate(total=Sum("montant_initial")).get("total") or Decimal("0.00")
    caisse_appro_total = ApprovisionnementCaisse.objects.filter(
        caissiere_id__in=caissiere_user_ids,
        nature_approvisionnement__in=[
            ApprovisionnementCaisse.NATURE_URGENCE_DG,
            ApprovisionnementCaisse.NATURE_CHEQUE_DIRECT,
        ]
    ).aggregate(total=Sum("montant")).get("total") or Decimal("0.00")
    caisse_sorties_maintenance_total = (
        Maintenance.objects.filter(statut="payee", paiement_saisi_par_id__in=caissiere_user_ids)
        .aggregate(total=Sum("total_facture"))
        .get("total")
        or Decimal("0.00")
    )
    caisse_sorties_depenses_total = (
        Depense.objects.filter(
            statut=Depense.STATUT_PAYEE,
            mode_reglement=Depense.MODE_ESPECE,
            paiement_saisi_par_id__in=caissiere_user_ids,
        )
        .aggregate(total=Sum("montant_engage"))
        .get("total")
        or Decimal("0.00")
    )
    dg_total_avances = (
        ApprovisionnementCaisse.objects.filter(
            nature_approvisionnement=ApprovisionnementCaisse.NATURE_URGENCE_DG
        ).aggregate(total=Sum("montant")).get("total")
        or Decimal("0.00")
    )
    dg_total_remboursements = (
        ApprovisionnementCaisse.objects.filter(
            nature_approvisionnement=ApprovisionnementCaisse.NATURE_RETRAIT_REMBOURSEMENT
        ).aggregate(total=Sum("montant")).get("total")
        or Decimal("0.00")
    )
    dg_solde_total = dg_total_avances - dg_total_remboursements
    caisse_solde_global = caisse_solde_initial_total + caisse_appro_total - caisse_sorties_maintenance_total - caisse_sorties_depenses_total
    caisse_appro_count = ApprovisionnementCaisse.objects.filter(caissiere_id__in=caissiere_user_ids).count()
    caisse_recent_appros = list(
        ApprovisionnementCaisse.objects.filter(caissiere_id__in=caissiere_user_ids).select_related("caissiere", "saisi_par").order_by("-date_approvisionnement", "-id")[:6]
    )
    caisse_recent_mouvements = []
    if user_role in {"caissiere", "caissiere_soni", "caissiere_avena"}:
        caissiere_user_ids = [request.user.id]
        caisse_solde_initial_total = (
            SoldeInitialCaisse.objects.filter(caissiere=request.user).aggregate(total=Sum("montant_initial")).get("total")
            or Decimal("0.00")
        )
        caisse_appro_total = (
            ApprovisionnementCaisse.objects.filter(
                caissiere=request.user,
                nature_approvisionnement__in=[
                    ApprovisionnementCaisse.NATURE_URGENCE_DG,
                    ApprovisionnementCaisse.NATURE_CHEQUE_DIRECT,
                ],
            ).aggregate(total=Sum("montant")).get("total")
            or Decimal("0.00")
        )
        caisse_sorties_maintenance_total = (
            Maintenance.objects.filter(statut="payee", paiement_saisi_par=request.user)
            .aggregate(total=Sum("total_facture"))
            .get("total")
            or Decimal("0.00")
        )
        caisse_sorties_depenses_total = (
            Depense.objects.filter(
                statut=Depense.STATUT_PAYEE,
                mode_reglement=Depense.MODE_ESPECE,
                paiement_saisi_par=request.user,
            )
            .aggregate(total=Sum("montant_engage"))
            .get("total")
            or Decimal("0.00")
        )
        caisse_solde_global = caisse_solde_initial_total + caisse_appro_total - caisse_sorties_maintenance_total - caisse_sorties_depenses_total
        caisse_recent_appros = list(
            ApprovisionnementCaisse.objects.filter(caissiere=request.user)
            .select_related("caissiere", "saisi_par")
            .order_by("-date_approvisionnement", "-id")[:6]
        )
    if user_role in {"caissiere", "caissiere_soni", "caissiere_avena", "comptable", "comptable_sogefi", "comptable_avena"}:
        maintenance_caisse_qs = Maintenance.objects.filter(
            statut="payee",
            paiement_saisi_par_id__in=caissiere_user_ids,
        ).order_by("-date_paiement", "-id")[:4]
        depenses_caisse_qs = Depense.objects.filter(
            statut=Depense.STATUT_PAYEE,
            mode_reglement=Depense.MODE_ESPECE,
            paiement_saisi_par_id__in=caissiere_user_ids,
        ).select_related("operation").order_by("-date_paiement", "-id")[:4]
        for maintenance in maintenance_caisse_qs:
            caisse_recent_mouvements.append(
                {
                    "date": maintenance.date_paiement,
                    "type": "Maintenance",
                    "reference": maintenance.reference,
                    "designation": maintenance.camion.numero_tracteur,
                    "montant": maintenance.total_facture or Decimal("0.00"),
                }
            )
        for depense in depenses_caisse_qs:
            caisse_recent_mouvements.append(
                {
                    "date": depense.date_paiement,
                    "type": "Depense BL" if depense.source_depense == Depense.SOURCE_CHARGEMENT else "Autre depense",
                    "reference": depense.reference,
                    "designation": depense.libelle_depense or depense.titre,
                    "montant": depense.montant_comptable,
                }
            )
        caisse_recent_mouvements.sort(
            key=lambda item: (item["date"] or today, item["reference"]),
            reverse=True,
        )
        caisse_recent_mouvements = caisse_recent_mouvements[:6]
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
    commercial_factures_emises = active_operations.exclude(
        Q(numero_facture__isnull=True) | Q(numero_facture="")
    )
    commercial_factures_a_emettre = active_operations.filter(etat_bon="livre").filter(
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
        active_operations.select_related("commande", "client", "produit", "camion")
        .filter(etat_bon="initie")
        .order_by("-date_creation")
    )
    comptable_facturation_queryset = (
        active_operations.select_related("commande", "client", "produit", "camion", "chauffeur")
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

    dernieres_operations = active_operations.select_related(
        "client",
        "camion",
        "chauffeur",
        "produit",
    ).order_by("-date_creation")[:8]
    alertes_operations = active_operations.select_related("client").filter(
        Q(date_bons_charges__isnull=False, date_bons_livres__isnull=True, date_bons_charges__lt=seuil_retard)
        | Q(date_bons_livres__isnull=False, date_bon_retour__isnull=True)
    ).order_by("-date_creation")[:6]

    top_clients = (
        active_operations.values("client__entreprise")
        .annotate(total_bons=Count("id"), total_quantite=Sum("quantite"))
        .order_by("-total_bons", "-total_quantite")[:5]
    )
    camions_plus_utilises = (
        Camion.objects.filter(operations__remplace_par__isnull=True)
        .values("numero_tracteur", "numero_citerne", "chauffeur__nom")
        .annotate(total_bons=Count("operations", filter=Q(operations__remplace_par__isnull=True), distinct=True))
        .order_by("-total_bons")[:5]
    )

    quantites_carburant = active_operations.aggregate(
        total_essence=Sum("quantite", filter=Q(produit__nom__icontains="essence")),
        total_gasoil=Sum("quantite", filter=Q(produit__nom__icontains="gasoil")),
    )
    total_essence = float(quantites_carburant["total_essence"] or 0)
    total_gasoil = float(quantites_carburant["total_gasoil"] or 0)

    daily_inities = (
        active_operations.filter(etat_bon="initie")
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
        receptions_logistique = active_operations.filter(etat_bon="attente_reception_logistique").count()
        if receptions_logistique:
            add_alert(
                "BL a receptionner",
                f"{receptions_logistique} BL liquide(s) attendent votre validation de reception logistique.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=attente_reception_logistique",
                "danger",
            )
        remises_chauffeur = active_operations.filter(etat_bon="liquide_logistique").count()
        if remises_chauffeur:
            add_alert(
                "BL a remettre au chauffeur",
                f"{remises_chauffeur} BL receptionne(s) attendent la remise chauffeur.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=liquide_logistique",
                "warning",
            )
        bl_charges = _dashboard_count_charged_bl_with_pending_depenses(
            active_operations.filter(etat_bon="charge")
        )
        if bl_charges:
            add_alert(
                "BL charges par le chauffeur",
                f"{bl_charges} BL charge(s) attendent encore la saisie ou la validation finale des depenses liees au chargement.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=charge&depense_niveau=action_requise",
                "ok",
            )
        bons_retour = active_operations.filter(etat_bon="livre", date_bon_retour__isnull=True).count()
        if bons_retour:
            add_alert(
                "Bons retour attendus",
                f"{bons_retour} BL livre(s) attendent encore le bon retour.",
                "Ouvrir chargement / livraison",
                "/operations/logisticien/?etat=livre&retour=attendu",
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
            active_operations.select_related("client", "camion", "chauffeur", "commande", "produit")
            .filter(etat_bon__in=["attente_reception_logistique", "liquide_logistique", "liquide_chauffeur", "charge", "livre"])
            .order_by("-date_creation")[:8]
        )
        logistique_maintenance_dashboard = list(
            Maintenance.objects.select_related("camion")
            .filter(statut__in=["en_cours", "attente_prix", "attente_dga", "attente_dg", "attente_paiement"])
            .order_by("-date_creation")[:6]
        )
    elif user_role == "chef_chauffeur":
        chargements = active_operations.filter(etat_bon="liquide_chauffeur").count()
        if chargements:
            add_alert(
                "BL a charger",
                f"{chargements} BL remis au chauffeur attendent le chargement.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=liquide_chauffeur",
                "danger",
            )
        livraisons = active_operations.filter(etat_bon="charge").count()
        if livraisons:
            add_alert(
                "BL a livrer",
                f"{livraisons} BL charge(s) attendent encore la livraison.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=charge",
                "warning",
            )
    elif user_role == "secretaire":
        bl_initie = active_operations.filter(etat_bon="initie").count()
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
        depenses_internes_soni_dga = Depense.objects.filter(
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_VALIDATION_DGA,
        ).count()
        if depenses_internes_soni_dga:
            add_alert(
                "Depenses internes SONI",
                f"{depenses_internes_soni_dga} depense(s) internes SONI attendent votre validation DGA.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_dga_engagement",
                "warning",
            )
    elif user_role in {"caissiere", "caissiere_soni", "caissiere_avena"}:
        maintenances_espece = Maintenance.objects.filter(
            statut="attente_paiement",
            mode_paiement=Maintenance.MODE_ESPECE,
        ).count()
        depenses_espece = Depense.objects.filter(
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            **(
                {"source_depense": Depense.SOURCE_GENERALE, "entite_depense": Depense.ENTITE_SONI}
                if user_role == "caissiere_soni"
                else {"source_depense": Depense.SOURCE_GENERALE, "entite_depense": Depense.ENTITE_AVENA}
                if user_role == "caissiere_avena"
                else {}
            ),
        ).count()
        if user_role in {"caissiere_soni", "caissiere_avena"}:
            maintenances_espece = 0
        total_paiements_caisse = maintenances_espece + depenses_espece
        if total_paiements_caisse:
            add_alert(
                "Paiements a traiter",
                f"{total_paiements_caisse} paiement(s) attendent votre traitement en caisse ({maintenances_espece} maintenance, {depenses_espece} depense).",
                "Ouvrir les paiements",
                "/maintenance/paiements/",
                "ok",
            )
    elif user_role == "comptable":
        demandes_nouveau_bl = DemandeNouveauBL.objects.filter(statut=DemandeNouveauBL.STATUT_EN_ATTENTE).count()
        if demandes_nouveau_bl:
            add_alert(
                "Nouveaux BL apres changement camion",
                f"{demandes_nouveau_bl} BL doivent etre recrees suite a un changement de camion par la logistique.",
                "Creer les BL",
                "/operations/comptable/",
                "danger",
            )
        depenses_cheque_soni = Depense.objects.filter(
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        ).count()
        if depenses_cheque_soni:
            add_alert(
                "Paiements SONI par cheque",
                f"{depenses_cheque_soni} depense(s) internes SONI attendent votre traitement comptable.",
                "Ouvrir les paiements",
                "/maintenance/paiements/",
                "warning",
            )
        commandes_a_transformer = Commande.objects.filter(statut="planifiee").exclude(operations__isnull=False).count()
        if commandes_a_transformer:
            add_alert(
                "BL a creer",
                f"{commandes_a_transformer} commande(s) validees attendent la creation du BL.",
                "Ouvrir operation comptable",
                "/operations/comptable/",
                "danger",
            )
        factures_a_traiter = active_operations.filter(etat_bon="livre").filter(
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
        maintenances_cheque = Maintenance.objects.filter(
            statut="attente_paiement",
            mode_paiement=Maintenance.MODE_CHEQUE,
        ).count()
        depenses_cheque = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE).count()
        total_paiements_cheque = maintenances_cheque + depenses_cheque
        if total_paiements_cheque:
            add_alert(
                "Paiements par cheque a traiter",
                f"{total_paiements_cheque} paiement(s) attendent votre traitement comptable ({maintenances_cheque} maintenance, {depenses_cheque} depense).",
                "Ouvrir les paiements",
                "/maintenance/paiements/",
                "warning",
            )
    elif user_role == "comptable_avena":
        depenses_cheque_avena = Depense.objects.filter(
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
        ).count()
        if depenses_cheque_avena:
            add_alert(
                "Paiements Avena par cheque",
                f"{depenses_cheque_avena} depense(s) internes Avena attendent votre traitement comptable.",
                "Ouvrir les paiements",
                "/maintenance/paiements/",
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
        engagements_dga = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DGA).count()
        if engagements_dga:
            add_alert(
                "Engagements a valider",
                f"{engagements_dga} depense(s) internes attendent votre validation DGA SOGEFI apres la saisie achat / prix.",
                "Ouvrir les depenses",
                "/depenses/?statut=attente_validation_dga_engagement",
                "warning",
            )
    elif user_role == "dga_avena":
        engagements_dga = Depense.objects.filter(
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_VALIDATION_DGA,
        ).count()
        if engagements_dga:
            add_alert(
                "Engagements Avena a valider",
                f"{engagements_dga} depense(s) internes Avena attendent votre validation DGA.",
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
                "/commandes/",
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
        bl_a_recevoir = active_operations.filter(etat_bon="attente_reception_transitaire").count()
        if bl_a_recevoir:
            add_alert(
                "BL a receptionner",
                f"{bl_a_recevoir} BL transmis par la secretaire attendent votre reception.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=attente_reception_transitaire",
                "danger",
            )
        bl_a_declarer = active_operations.filter(etat_bon="transmis").count()
        if bl_a_declarer:
            add_alert(
                "BL a declarer",
                f"{bl_a_declarer} BL recus attendent la declaration transitaire.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=transmis",
                "warning",
            )
        bl_a_liquider = active_operations.filter(etat_bon="declare").count()
        if bl_a_liquider:
            add_alert(
                "BL a liquider",
                f"{bl_a_liquider} BL declares attendent la liquidation.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=declare",
                "warning",
            )
        bl_liquides = active_operations.filter(etat_bon="liquide").count()
        if bl_liquides:
            add_alert(
                "BL liquides a orienter",
                f"{bl_liquides} BL liquides attendent soit un transfert logistique, soit un chargement direct.",
                "Ouvrir le transitaire",
                "/operations/transitaire/?etat=liquide",
                "ok",
            )
        transitaire_operations_dashboard = list(
            active_operations.select_related("client", "camion", "chauffeur", "commande")
            .filter(etat_bon__in=["attente_reception_transitaire", "transmis", "declare", "liquide"])
            .order_by("-date_creation")[:8]
        )
    elif user_role == "chef_chauffeur":
        bl_a_charger = active_operations.filter(etat_bon="liquide_chauffeur").count()
        if bl_a_charger:
            add_alert(
                "BL a charger",
                f"{bl_a_charger} BL attendent votre chargement.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=liquide_chauffeur",
                "danger",
            )
        bl_a_livrer = active_operations.filter(etat_bon="charge").count()
        if bl_a_livrer:
            add_alert(
                "BL a livrer",
                f"{bl_a_livrer} BL deja charges attendent maintenant la livraison.",
                "Ouvrir chef chauffeur",
                "/operations/chef-chauffeur/?etat=charge",
                "warning",
            )
        chef_chauffeur_operations_dashboard = list(
            active_operations.select_related("client", "camion", "chauffeur", "commande")
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
        "depenses_attente_cheque": depenses_attente_cheque,
        "depenses_attente_paiement": depenses_attente_paiement,
        "depenses_internes_total": depenses_internes_total,
        "depenses_internes_attente_achat": depenses_internes_attente_achat,
        "depenses_internes_attente_dga": depenses_internes_attente_dga,
        "depenses_internes_attente_dg": depenses_internes_attente_dg,
        "depenses_internes_attente_paiement": depenses_internes_attente_paiement,
        "depenses_internes_attente_caisse": depenses_internes_attente_caisse,
        "depenses_internes_attente_comptable": depenses_internes_attente_comptable,
        "depenses_internes_payees": depenses_internes_payees,
        "depenses_internes_montant_total": depenses_internes_montant_total,
        "depenses_internes_montant_en_attente": depenses_internes_montant_en_attente,
        "depenses_internes_recentes": depenses_internes_recentes,
        "caisse_solde_initial_total": caisse_solde_initial_total,
        "caisse_appro_total": caisse_appro_total,
        "caisse_sorties_maintenance_total": caisse_sorties_maintenance_total,
        "caisse_sorties_depenses_total": caisse_sorties_depenses_total,
        "caisse_solde_global": caisse_solde_global,
        "caisse_appro_count": caisse_appro_count,
        "caisse_recent_appros": caisse_recent_appros,
        "caisse_recent_mouvements": caisse_recent_mouvements,
        "dg_total_avances": dg_total_avances,
        "dg_total_remboursements": dg_total_remboursements,
        "dg_solde_total": dg_solde_total,
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
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "invite",
    "logistique",
    "maintenancier",
    "dga",
    "dga_sogefi",
    "dga_avena",
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
