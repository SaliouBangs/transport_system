from io import BytesIO
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from camions.models import Camion, Transporteur
from clients.models import Client
from clients.models import total_encaisse_sur_commande
from depenses.models import Depense
from operations.models import HistoriqueAffectationOperation, Operation
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_user_role, is_admin_user, role_required

from .forms import (
    AffreteCamionExistantForm,
    AffreteCreationForm,
    CommandeAffectationForm,
    CommandeForm,
    CommandeNumeroForm,
)
from .models import Commande

COMMANDE_STATUS_FILTERS = [
    ("attente_validation_dga", "En attente validation DGA"),
    ("validee_dga", "Validee par DGA"),
    ("rejetee_dga", "Rejetee par DGA"),
    ("attente_validation_dg", "En attente validation DG"),
    ("validee_dg", "Validee par DG"),
    ("rejetee_dg", "Rejetee par DG"),
    ("initie", "Initie"),
    ("declare", "Declare"),
    ("liquide", "Liquide"),
    ("charge", "Charge"),
    ("livre", "Livre"),
    ("bon_retour", "Livre / retourne"),
]

OPERATION_REPORT_FIELDS = {
    "declare": ("date_bons_declares", "Date declare"),
    "liquide": ("date_bons_liquides", "Date liquide"),
    "charge": ("date_bons_charges", "Date charge"),
    "livre": ("date_bons_livres", "Date livre"),
}

RAPPORT_GLOBAL_STATUS_CHOICES = [
    ("", "Tous les etats exacts"),
    ("attente_validation_dga", "En attente validation DGA"),
    ("validee_dga", "Validee par DGA"),
    ("rejetee_dga", "Rejetee par DGA"),
    ("attente_validation_dg", "En attente validation DG"),
    ("validee_dg", "Validee par DG"),
    ("rejetee_dg", "Rejetee par DG"),
    ("initie", "Initie"),
    ("attente_reception_transitaire", "BL secretaire"),
    ("transmis", "Transmis"),
    ("declare", "Declare"),
    ("liquide", "Liquide"),
    ("liquides_tous", "Tous les etats liquides"),
    ("attente_reception_logistique", "BL liquide transitaire"),
    ("liquide_logistique", "BL liquides logistique"),
    ("liquide_chauffeur", "BL liquide chauffeur"),
    ("charge", "Charge"),
    ("livre", "Livre"),
    ("bon_retour", "Livres / retournes"),
]

OPERATION_STATUS_DATE_FIELDS = {
    "initie": "date_bl",
    "attente_reception_transitaire": "date_transmission_depot",
    "transmis": "date_reception_transitaire",
    "declare": "date_bons_declares",
    "liquide": "date_bons_liquides",
    "attente_reception_logistique": "date_transfert_logistique",
    "liquide_logistique": "date_reception_logistique",
    "liquide_chauffeur": "date_remise_chauffeur",
    "charge": "date_bons_charges",
    "livre": "date_bons_livres",
}


def _commande_editable_before_dga(commande):
    return not commande.date_validation_dga and not commande.decision_dga


def _commande_label(commande):
    return commande.reference_affichee


def _build_dma_alert(client, montant_commande=None, risque_actuel=None):
    if not client:
        return {
            "depasse_dma": False,
            "plafond_dma": Decimal("0.00"),
            "risque_actuel": Decimal("0.00"),
            "risque_projete": Decimal("0.00"),
            "depassement_dma": Decimal("0.00"),
            "ratio_projete": 0,
        }

    plafond_dma = client.decouvert_maximum_autorise or Decimal("0.00")
    risque_base = Decimal(risque_actuel if risque_actuel is not None else (client.risque_client or Decimal("0.00")))
    montant = Decimal(montant_commande or Decimal("0.00"))
    risque_projete = risque_base + montant
    depassement_dma = max(Decimal("0.00"), risque_projete - plafond_dma) if plafond_dma > 0 else Decimal("0.00")
    ratio_projete = 0
    if plafond_dma > 0:
        ratio_projete = int((risque_projete / plafond_dma) * 100)
    return {
        "depasse_dma": plafond_dma > 0 and risque_projete > plafond_dma,
        "plafond_dma": plafond_dma,
        "risque_actuel": risque_base,
        "risque_projete": risque_projete,
        "depassement_dma": depassement_dma,
        "ratio_projete": max(0, ratio_projete),
    }


def _client_commande_payload(client, etat_filtre="", date_debut="", date_fin=""):
    commandes_queryset = client.commandes.all().prefetch_related("operations").order_by("-date_creation")
    encaissements_queryset = client.encaissements.all().order_by("-date_encaissement", "-id")
    operation_statuses = {"initie", "declare", "liquide", "charge", "livre"}
    encours_reel = client.encours_client or Decimal("0.00")
    engagement_total = client.engagement_client or Decimal("0.00")
    engagement_net = client.engagement_net or Decimal("0.00")
    paiements_anticipes = client.paiements_anticipes or Decimal("0.00")
    creance_client = client.creance_client or Decimal("0.00")
    risque_client = client.risque_client or Decimal("0.00")

    if date_debut:
        commandes_queryset = commandes_queryset.filter(date_commande__gte=date_debut)
        encaissements_queryset = encaissements_queryset.filter(date_encaissement__gte=date_debut)
    if date_fin:
        commandes_queryset = commandes_queryset.filter(date_commande__lte=date_fin)
        encaissements_queryset = encaissements_queryset.filter(date_encaissement__lte=date_fin)

    total_quantite = Decimal("0.00")
    total_montant = Decimal("0.00")
    commandes_filtrees = []
    commandes_rejetees_dg = []
    for commande in commandes_queryset:
        latest_operation = (
            commande.operations.filter(remplace_par__isnull=True).order_by("-date_creation").first()
            or commande.operations.order_by("-date_creation").first()
        )
        if etat_filtre:
            if etat_filtre in operation_statuses:
                if not latest_operation or latest_operation.etat_bon != etat_filtre:
                    continue
            elif commande.statut != etat_filtre:
                continue
        quantite = commande.quantite or Decimal("0.00")
        prix = commande.prix_negocie or Decimal("0.00")
        montant_commande = quantite * prix
        total_paye = total_encaisse_sur_commande(commande)
        solde_commande = max(Decimal("0.00"), montant_commande - total_paye)
        display_status = commande.get_statut_display()
        status_key = commande.statut
        if latest_operation and latest_operation.etat_bon in operation_statuses:
            display_status = latest_operation.get_etat_bon_display()
            status_key = latest_operation.etat_bon
        item = {
            "reference": commande.reference_affichee,
            "status_key": status_key,
            "statut": display_status,
            "montant": str(montant_commande),
            "paiement": str(total_paye),
            "solde": str(solde_commande),
            "quantite": str(quantite),
            "produit": commande.produit.nom if commande.produit_id else "",
            "date": commande.date_commande.strftime("%Y-%m-%d") if commande.date_commande else "",
        }
        if commande.statut == "rejetee_dg":
            commandes_rejetees_dg.append(item)
        else:
            total_quantite += quantite
            total_montant += montant_commande
            commandes_filtrees.append(item)

    derniers_encaissements = []
    total_paiements_filtres = Decimal("0.00")
    for encaissement in encaissements_queryset[:8]:
        total_paiements_filtres += encaissement.montant or Decimal("0.00")
        derniers_encaissements.append(
            {
                "date": encaissement.date_encaissement.strftime("%Y-%m-%d") if encaissement.date_encaissement else "",
                "montant": str(encaissement.montant or Decimal("0.00")),
                "mode": encaissement.get_mode_paiement_display(),
                "reference": encaissement.reference or "",
            }
        )

    disponible_decouvert = (client.decouvert_maximum_autorise or Decimal("0.00")) - risque_client
    return {
        "id": client.id,
        "nom": client.nom,
        "entreprise": client.entreprise,
        "telephone": client.telephone,
        "ville": client.ville,
        "fonction_contact": client.fonction_contact,
        "solde_initial": str(client.solde_initial or Decimal("0.00")),
        "date_solde_initial": client.date_solde_initial.strftime("%Y-%m-%d") if client.date_solde_initial else "",
        "delai_paiement_jours": client.delai_paiement_jours or 0,
        "encours_client": str(encours_reel),
        "encours_reel": str(encours_reel),
        "risque_client": str(risque_client),
        "total_commandes_livrees": str(client.total_commandes_livrees or Decimal("0.00")),
        "creance_client": str(creance_client),
        "total_paiements": str(client.total_paiements or Decimal("0.00")),
        "paiements_anticipes": str(paiements_anticipes),
        "decouvert_maximum_autorise": str(client.decouvert_maximum_autorise or Decimal("0.00")),
        "disponible_decouvert": str(disponible_decouvert),
        "ratio_decouvert": float(client.ratio_decouvert or 0),
        "niveau_risque": client.niveau_risque,
        "total_quantite_filtre": str(total_quantite),
        "total_montant_filtre": str(total_montant),
        "nombre_commandes_filtrees": len(commandes_filtrees),
        "total_paiements_filtres": str(total_paiements_filtres),
        "engagement_total": str(engagement_total),
        "engagement_net": str(engagement_net),
        "destinations": [destination.adresse for destination in client.destinations.all() if destination.adresse],
        "dernieres_commandes": commandes_filtrees,
        "commandes_rejetees_dg": commandes_rejetees_dg,
        "derniers_encaissements": derniers_encaissements,
    }


def _generate_archived_bl_number(numero_bl):
    base = numero_bl or "BL"
    suffix = 1
    candidate = f"{base}-ANCIEN"
    while Operation.objects.filter(numero_bl=candidate).exists():
        suffix += 1
        candidate = f"{base}-ANCIEN-{suffix}"
    return candidate


def _clone_operation_for_new_truck(operation, camion, chauffeur):
    numero_bl_original = operation.numero_bl
    operation.numero_bl = _generate_archived_bl_number(numero_bl_original)
    operation.save(update_fields=["numero_bl"])

    nouvelle_operation = Operation.objects.create(
        numero_bl=numero_bl_original,
        etat_bon=operation.etat_bon,
        commande=operation.commande,
        reference_externe=operation.reference_externe,
        regime_douanier=operation.regime_douanier,
        depot=operation.depot,
        client=operation.client,
        destination=operation.destination,
        camion=camion,
        chauffeur=chauffeur,
        produit=operation.produit,
        quantite=operation.quantite,
        date_bl=operation.date_bl,
        date_transmission_depot=operation.date_transmission_depot,
        date_bons_declares=operation.date_bons_declares,
        date_bons_liquides=operation.date_bons_liquides,
        date_bons_charges=operation.date_bons_charges,
        date_bons_livres=operation.date_bons_livres,
        date_bon_retour=operation.date_bon_retour,
        date_decharge_chauffeur=operation.date_decharge_chauffeur,
        heure_decharge_chauffeur=operation.heure_decharge_chauffeur,
        livreur=operation.livreur,
        numero_facture=operation.numero_facture,
        date_facture=operation.date_facture,
        montant_facture=operation.montant_facture,
        observation=operation.observation,
    )
    operation.remplace_par = nouvelle_operation
    operation.save(update_fields=["remplace_par"])
    HistoriqueAffectationOperation.objects.create(
        operation=nouvelle_operation,
        ancien_camion=operation.camion,
        ancien_chauffeur=operation.chauffeur,
        ancien_livreur=operation.livreur,
        ancienne_date_decharge_chauffeur=operation.date_decharge_chauffeur,
        ancienne_heure_decharge_chauffeur=operation.heure_decharge_chauffeur,
        ancien_etat_bon=operation.etat_bon,
        nouveau_camion=camion,
        nouveau_chauffeur=chauffeur,
    )
    return nouvelle_operation


def _commandes_queryset(request):
    user_role = get_user_role(request.user)
    is_admin = request.user.is_superuser
    query = request.GET.get("q", "").strip()
    statut = request.GET.get("statut", "").strip()
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()
    scope = request.GET.get("scope", "").strip() or "actives"

    commandes = (
        Commande.objects.select_related("client", "client__commercial", "produit", "camion", "chauffeur")
        .prefetch_related(
            Prefetch(
                "operations",
                queryset=Operation.objects.select_related("camion", "chauffeur").prefetch_related("historiques_affectation").order_by("-date_creation"),
            )
        )
        .order_by("-date_creation")
    )
    if user_role == "commercial" and not is_admin:
        commandes = commandes.filter(client__commercial=request.user)
    if user_role == "responsable_commercial" and not is_admin:
        commandes = commandes.filter(client__commercial__isnull=False)
    if is_admin:
        pass
    elif user_role in {"commercial", "responsable_commercial"}:
        if scope == "historique":
            pass
        else:
            commandes = commandes.filter(statut="validee_dg").filter(Q(reference__isnull=True) | Q(reference=""))
    elif user_role == "logistique":
        if scope == "historique":
            commandes = commandes.exclude(statut="validee_dg", reference__isnull=False).exclude(statut="validee_dg", reference="")
        else:
            commandes = commandes.filter(statut="validee_dg").exclude(reference__isnull=True).exclude(reference="")
    elif user_role == "dga":
        if scope == "historique":
            commandes = commandes.exclude(statut="attente_validation_dga")
        else:
            commandes = commandes.filter(statut="attente_validation_dga")
    elif user_role == "directeur":
        if scope == "historique":
            commandes = commandes.exclude(statut="attente_validation_dg")
        else:
            commandes = commandes.filter(statut="attente_validation_dg")
    else:
        if scope == "historique":
            commandes = commandes.exclude(statut="attente_validation_dga")
        else:
            commandes = commandes.filter(statut="attente_validation_dga")
    if query:
        commandes = commandes.filter(
            Q(reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(client__nom__icontains=query)
            | Q(produit__nom__icontains=query)
            | Q(operations__numero_bl__icontains=query)
            | Q(ville_depart__icontains=query)
            | Q(ville_arrivee__icontains=query)
        )
    if (date_debut or date_fin) and not statut:
        if date_debut:
            commandes = commandes.filter(date_commande__gte=date_debut)
        if date_fin:
            commandes = commandes.filter(date_commande__lte=date_fin)

    commandes = commandes.distinct()
    commandes_list = list(commandes)
    for commande in commandes_list:
        latest_operation = commande.operations.all()[0] if getattr(commande, "operations", None) and commande.operations.all() else None
        commande.latest_operation = latest_operation
        commande.requires_sage_number = commande.statut == "validee_dg" and not commande.reference
        commande.current_status_data = _commande_exact_status_data(commande)
        commande.report_status_date = None
        commande.report_status_operation = latest_operation
        if statut in OPERATION_REPORT_FIELDS:
            field_name = OPERATION_REPORT_FIELDS[statut][0]
            matching_operations = [operation for operation in commande.operations.all() if getattr(operation, field_name)]
            if matching_operations:
                matching_operations.sort(key=lambda operation: getattr(operation, field_name), reverse=True)
                commande.report_status_operation = matching_operations[0]
                commande.report_status_date = getattr(matching_operations[0], field_name)
        commande.has_reaffectation = bool(
            latest_operation and latest_operation.historiques_affectation.all()
        )
        commande.latest_reaffectation = (
            latest_operation.historiques_affectation.all()[0]
            if latest_operation and latest_operation.historiques_affectation.all()
            else None
        )

    if statut:
        commandes_list = [
            commande
            for commande in commandes_list
            if getattr(commande, "current_status_data", {}).get("key") == statut
        ]

    if date_debut or date_fin:
        filtered_commandes = []
        for commande in commandes_list:
            if statut:
                date_reference = getattr(commande, "current_status_data", {}).get("date")
            else:
                date_reference = commande.date_commande
            if date_reference is None:
                continue
            date_value = date_reference.date() if hasattr(date_reference, "date") else date_reference
            if date_debut and str(date_value) < date_debut:
                continue
            if date_fin and str(date_value) > date_fin:
                continue
            filtered_commandes.append(commande)
        commandes_list = filtered_commandes

    return commandes_list, query, statut, date_debut, date_fin, scope, user_role


def liste_commandes(request):
    commandes, query, statut, date_debut, date_fin, scope, user_role = _commandes_queryset(request)
    commandes_stats = _build_commandes_stats(commandes, statut, date_debut, date_fin)
    report_status_label = OPERATION_REPORT_FIELDS.get(statut, ("", ""))[1] if statut in OPERATION_REPORT_FIELDS else ""
    logistique_rows = []
    if user_role == "logistique":
        for commande in commandes:
            operations = list(commande.operations.all())
            if operations:
                for operation in operations:
                    logistique_rows.append(
                        {
                            "commande": commande,
                            "operation": operation,
                            "camion": operation.camion or commande.camion,
                            "chauffeur": operation.chauffeur or commande.chauffeur,
                            "niveau_bon_label": operation.get_etat_bon_display(),
                            "has_reaffectation": bool(operation.remplace_par_id or operation.anciennes_versions.all()),
                            "latest_reaffectation": operation.historiques_affectation.all()[0] if operation.historiques_affectation.all() else None,
                            "button_label": "Lecture seule" if operation.etat_bon == "livre" else "Changer de camion" if operation.etat_bon in {"declare", "liquide", "charge"} else "Affecter camion",
                            "can_change_truck": operation.etat_bon != "livre",
                        }
                    )
            else:
                logistique_rows.append(
                    {
                        "commande": commande,
                        "operation": None,
                        "camion": commande.camion,
                        "chauffeur": commande.chauffeur,
                        "niveau_bon_label": "Aucun BL",
                        "has_reaffectation": False,
                        "latest_reaffectation": None,
                        "button_label": "Affecter camion",
                        "can_change_truck": True,
                        }
                    )
    return render(
        request,
        "commandes/commandes.html",
            {
                "commandes": commandes,
                "commandes_stats": commandes_stats,
                "logistique_rows": logistique_rows,
                "query": query,
                "statut": statut,
            "date_debut": date_debut,
            "date_fin": date_fin,
                "scope": scope,
                "page_user_role": user_role,
                "statut_choices": COMMANDE_STATUS_FILTERS,
                "report_status_label": report_status_label,
                "current_filters": request.GET.urlencode(),
            },
        )


def _build_commandes_stats(commandes, statut, date_debut, date_fin):
    product_totals = {}
    camion_ids = set()

    for commande in commandes:
        produit_label = (
            commande.produit.nom.upper()
            if getattr(commande, "produit", None) and commande.produit.nom
            else "AUTRE"
        )
        product_totals.setdefault(
            produit_label,
            {
                "label": produit_label,
                "quantity": Decimal("0"),
                "unit": "L" if produit_label in {"ESSENCE", "GASOIL"} else "",
            },
        )
        product_totals[produit_label]["quantity"] += commande.quantite or Decimal("0")

        camion = None
        if getattr(commande, "latest_operation", None) and commande.latest_operation.camion_id:
            camion = commande.latest_operation.camion
        elif commande.camion_id:
            camion = commande.camion
        if camion:
            camion_ids.add(camion.id)

    status_labels = {
        "attente_validation_dga": "en attente de validation DGA",
        "validee_dga": "validées par DGA",
        "rejetee_dga": "rejetées par DGA",
        "attente_validation_dg": "en attente de validation DG",
        "validee_dg": "validées par DG",
        "rejetee_dg": "rejetées par DG",
        "initie": "initiées",
        "declare": "déclarées",
        "liquide": "liquidées",
        "charge": "chargées",
        "livre": "livrées",
    }
    stats_title = (
        f"Qté totale des commandes {status_labels.get(statut, statut)}"
        if statut
        else "Qté totale des commandes"
    )

    if date_debut and date_fin:
        stats_period = f"du {date_debut}" if date_debut == date_fin else f"du {date_debut} au {date_fin}"
    elif date_debut:
        stats_period = f"à partir du {date_debut}"
    elif date_fin:
        stats_period = f"jusqu'au {date_fin}"
    else:
        stats_period = "sur la sélection courante"

    product_stats = sorted(
        product_totals.values(),
        key=lambda item: (item["quantity"], item["label"]),
        reverse=True,
    )
    max_quantity = max((item["quantity"] for item in product_stats), default=Decimal("0"))

    for item in product_stats:
        item["fill_percent"] = 0
        if max_quantity > 0:
            item["fill_percent"] = max(12, min(100, int((item["quantity"] / max_quantity) * 100)))
        item["display_quantity"] = f"{item['quantity']:,.0f}".replace(",", " ")
        item["barrel_class"] = "barrel-essence" if item["label"] == "ESSENCE" else "barrel-gasoil"

    return {
        "title": stats_title,
        "period": stats_period,
        "product_stats": product_stats,
        "truck_count": len(camion_ids),
        "display_total_quantity": f"{sum((item['quantity'] for item in product_stats), Decimal('0')):,.0f}".replace(",", " "),
        "has_data": bool(product_stats),
    }


def _commande_status_display(commande):
    latest_operation = getattr(commande, "latest_operation", None)
    if latest_operation:
        if latest_operation.date_bon_retour:
            return "Livre / retourne"
        if latest_operation.etat_bon == "livre" and not latest_operation.date_bon_retour:
            return "Livre en attente de bon retour"
        return latest_operation.get_etat_bon_display()

    if commande.statut == "attente_validation_dga":
        return "En attente validation DGA"
    if commande.statut == "attente_validation_dg":
        if commande.decision_dga == "rejetee":
            return "Rejetee par DGA"
        if commande.decision_dga == "validee":
            return "Validee par DGA"
        return "En attente validation DG"
    if commande.statut == "validee_dg":
        return "Validee par DG"
    if commande.statut == "rejetee_dg":
        return "Rejetee par DG"
    return commande.get_statut_display()


def _commande_exact_status_data(commande):
    latest_operation = getattr(commande, "latest_operation", None)
    if latest_operation:
        if latest_operation.date_bon_retour:
            return {
                "key": "bon_retour",
                "label": "Livre / retourne",
                "date": latest_operation.date_bon_retour,
            }
        if latest_operation.etat_bon == "livre":
            return {
                "key": "livre",
                "label": "Livre en attente de bon retour",
                "date": latest_operation.date_bons_livres,
            }
        date_field = OPERATION_STATUS_DATE_FIELDS.get(latest_operation.etat_bon, "")
        return {
            "key": latest_operation.etat_bon,
            "label": latest_operation.get_etat_bon_display(),
            "date": getattr(latest_operation, date_field, None) if date_field else None,
        }

    if commande.statut == "attente_validation_dga":
        return {
            "key": "attente_validation_dga",
            "label": "En attente validation DGA",
            "date": commande.date_commande,
        }
    if commande.statut == "attente_validation_dg":
        if commande.decision_dga == "rejetee":
            return {
                "key": "rejetee_dga",
                "label": "Rejetee par DGA",
                "date": commande.date_validation_dga or commande.date_commande,
            }
        if commande.decision_dga == "validee":
            return {
                "key": "validee_dga",
                "label": "Validee par DGA",
                "date": commande.date_validation_dga or commande.date_commande,
            }
        return {
            "key": "attente_validation_dg",
            "label": "En attente validation DG",
            "date": commande.date_validation_dga or commande.date_commande,
        }
    if commande.statut == "validee_dg":
        return {
            "key": "validee_dg",
            "label": "Validee par DG",
            "date": commande.date_validation_dg or commande.date_commande,
        }
    if commande.statut == "rejetee_dg":
        return {
            "key": "rejetee_dg",
            "label": "Rejetee par DG",
            "date": commande.date_validation_dg or commande.date_commande,
        }
    return {
        "key": commande.statut,
        "label": _commande_status_display(commande),
        "date": commande.date_commande,
    }


def _rapport_global_base_queryset():
    return (
        Commande.objects.select_related(
            "client",
            "client__commercial",
            "produit",
            "camion",
            "chauffeur",
        )
        .prefetch_related(
            Prefetch(
                "operations",
                queryset=Operation.objects.select_related("camion", "chauffeur").order_by("-date_creation"),
            )
        )
        .order_by("-date_creation")
    )


def _build_rapport_global_context(request):
    query = request.GET.get("q", "").strip()
    selected_etats = [value.strip() for value in request.GET.getlist("etat") if value.strip()]
    date_debut = request.GET.get("date_debut", "").strip()
    date_fin = request.GET.get("date_fin", "").strip()
    client_id = request.GET.get("client", "").strip()
    produit_id = request.GET.get("produit", "").strip()
    affrete_id = request.GET.get("affrete", "").strip()

    queryset = _rapport_global_base_queryset()
    if query:
        queryset = queryset.filter(
            Q(reference__icontains=query)
            | Q(client__entreprise__icontains=query)
            | Q(client__nom__icontains=query)
            | Q(produit__nom__icontains=query)
            | Q(ville_depart__icontains=query)
            | Q(ville_arrivee__icontains=query)
            | Q(operations__numero_bl__icontains=query)
            | Q(camion__numero_tracteur__icontains=query)
            | Q(chauffeur__nom__icontains=query)
        )
    if client_id.isdigit():
        queryset = queryset.filter(client_id=client_id)
    if produit_id.isdigit():
        queryset = queryset.filter(produit_id=produit_id)
    commandes = list(queryset.distinct())
    lignes = []
    total_montant = Decimal("0.00")
    total_quantite = Decimal("0.00")
    total_livrees = 0
    total_chargees = 0
    total_bl = 0

    for commande in commandes:
        operations = list(commande.operations.all())
        latest_operation = operations[0] if operations else None
        commande.latest_operation = latest_operation
        status_data = _commande_exact_status_data(commande)
        status_date = status_data["date"]

        if selected_etats:
            matches_selected_status = False
            for etat in selected_etats:
                if etat == "bon_retour":
                    if latest_operation and latest_operation.date_bon_retour:
                        matches_selected_status = True
                        break
                elif etat == "liquides_tous":
                    if latest_operation and latest_operation.etat_bon in {
                        "liquide",
                        "attente_reception_logistique",
                        "liquide_logistique",
                        "liquide_chauffeur",
                    }:
                        matches_selected_status = True
                        break
                elif status_data["key"] == etat:
                    matches_selected_status = True
                    break
            if not matches_selected_status:
                continue
        if date_debut and (not status_date or status_date.strftime("%Y-%m-%d") < date_debut):
            continue
        if date_fin and (not status_date or status_date.strftime("%Y-%m-%d") > date_fin):
            continue

        commande.exact_status_key = status_data["key"]
        commande.exact_status_label = status_data["label"]
        commande.exact_status_date = status_date
        commande.current_bl = latest_operation.numero_bl if latest_operation else "-"
        commande.current_camion = latest_operation.camion if latest_operation and latest_operation.camion_id else commande.camion
        commande.current_chauffeur = latest_operation.chauffeur if latest_operation and latest_operation.chauffeur_id else commande.chauffeur
        commande.current_commercial = commande.client.commercial if commande.client_id else None
        commande.current_valorisation = commande.montant_commande or Decimal("0.00")
        commande.current_affrete = None
        commande.current_transporteur_label = "SOGEFI"
        if commande.current_camion and getattr(commande.current_camion, "est_affrete", False):
            commande.current_affrete = commande.current_camion.transporteur
            if commande.current_affrete:
                commande.current_transporteur_label = commande.current_affrete.nom

        if affrete_id:
            if affrete_id == "sogefi" and commande.current_affrete:
                continue
            if affrete_id == "affretes" and not commande.current_affrete:
                continue
            if affrete_id.isdigit():
                if not commande.current_affrete or str(commande.current_affrete.id) != affrete_id:
                    continue

        total_montant += commande.current_valorisation
        total_quantite += commande.quantite or Decimal("0.00")
        if latest_operation:
            total_bl += 1
            if latest_operation.etat_bon == "charge":
                total_chargees += 1
            if latest_operation.etat_bon == "livre":
                total_livrees += 1

        lignes.append(commande)

    produits_filter = []
    seen_produits = set()
    for commande in Commande.objects.exclude(produit__isnull=True).select_related("produit").order_by("produit__nom"):
        if commande.produit_id in seen_produits:
            continue
        seen_produits.add(commande.produit_id)
        produits_filter.append(commande.produit)

    affretes_filter = list(
        Transporteur.objects.filter(camions__est_affrete=True).distinct().order_by("nom")
    )

    return {
        "commandes": lignes,
        "query": query,
        "selected_etats": selected_etats,
        "current_filters": request.GET.urlencode(),
        "date_debut": date_debut,
        "date_fin": date_fin,
        "client_id": client_id,
        "produit_id": produit_id,
        "affrete_id": affrete_id,
        "clients_filter": Client.objects.order_by("entreprise", "nom"),
        "produits_filter": produits_filter,
        "affretes_filter": affretes_filter,
        "transporteur_filter_label": (
            "SOGEFI"
            if affrete_id == "sogefi"
            else "Tous les affretes"
            if affrete_id == "affretes"
            else next((item.nom for item in affretes_filter if str(item.id) == affrete_id), "")
        ),
        "status_choices": RAPPORT_GLOBAL_STATUS_CHOICES,
        "selected_etat_labels": [
            label for value, label in RAPPORT_GLOBAL_STATUS_CHOICES if value and value in selected_etats
        ],
        "stats": {
            "total_commandes": len(lignes),
            "total_montant": total_montant,
            "total_quantite": total_quantite,
            "total_bl": total_bl,
            "total_chargees": total_chargees,
            "total_livrees": total_livrees,
        },
    }


def rapport_global(request):
    return render(
        request,
        "commandes/rapport_global.html",
        _build_rapport_global_context(request),
    )


def detail_rapport_global(request, id):
    commande = get_object_or_404(
        Commande.objects.select_related(
            "client",
            "client__commercial",
            "produit",
            "camion",
            "chauffeur",
        ).prefetch_related(
            Prefetch(
                "operations",
                queryset=Operation.objects.select_related("camion", "chauffeur").prefetch_related(
                    Prefetch(
                        "depenses_liees",
                        queryset=Depense.objects.select_related("demandeur").prefetch_related("lignes").order_by("-date_creation"),
                    )
                ).order_by("-date_creation"),
            )
        ),
        id=id,
    )
    operations = list(commande.operations.all())
    latest_operation = operations[0] if operations else None
    commande.latest_operation = latest_operation
    exact_status = _commande_exact_status_data(commande)
    commande.exact_status_label = exact_status["label"]
    commande.exact_status_date = exact_status["date"]

    depenses_liees = []
    for operation in operations:
        for depense in operation.depenses_liees.all():
            depenses_liees.append(depense)

    return render(
        request,
        "commandes/detail_rapport_global.html",
        {
            "commande": commande,
            "operations": operations,
            "depenses_liees": depenses_liees,
            "client_payload": _client_commande_payload(commande.client),
            "return_url": "/commandes/rapport-global/",
        },
    )


def export_rapport_global_xls(request):
    try:
        from openpyxl import Workbook
    except ImportError:
        return HttpResponse(
            "Le module openpyxl n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    context = _build_rapport_global_context(request)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rapport global"
    sheet.append(
        [
            "Commande",
            "BL actuel",
            "Client",
            "Contact",
            "Produit",
            "Quantite",
            "Valorisation",
            "Etat exact",
            "Commercial",
            "Camion",
            "Chauffeur",
            "Date commande",
            "Date etat actuel",
            "Date declaration",
            "Date liquidation",
            "Date charge",
            "Date livre",
            "Date bon retour",
        ]
    )

    for commande in context["commandes"]:
        operation = commande.latest_operation
        sheet.append(
            [
                commande.reference_affichee,
                commande.current_bl,
                commande.client.entreprise,
                commande.client.nom or "",
                commande.produit.nom if commande.produit_id else "",
                float(commande.quantite) if commande.quantite is not None else "",
                float(commande.current_valorisation) if commande.current_valorisation is not None else "",
                commande.exact_status_label,
                commande.current_commercial.username if commande.current_commercial else "",
                commande.current_camion.numero_tracteur if commande.current_camion else "",
                commande.current_chauffeur.nom if commande.current_chauffeur else "",
                commande.date_commande.strftime("%Y-%m-%d") if commande.date_commande else "",
                commande.exact_status_date.strftime("%Y-%m-%d") if commande.exact_status_date else "",
                operation.date_bons_declares.strftime("%Y-%m-%d") if operation and operation.date_bons_declares else "",
                operation.date_bons_liquides.strftime("%Y-%m-%d") if operation and operation.date_bons_liquides else "",
                operation.date_bons_charges.strftime("%Y-%m-%d") if operation and operation.date_bons_charges else "",
                operation.date_bons_livres.strftime("%Y-%m-%d") if operation and operation.date_bons_livres else "",
                operation.date_bon_retour.strftime("%Y-%m-%d") if operation and operation.date_bon_retour else "",
            ]
        )

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_global_commandes.xlsx"'
    workbook.save(response)
    return response


def export_rapport_global_pdf(request):
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

    context = _build_rapport_global_context(request)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))

    data = [[
        "Commande",
        "BL",
        "Client",
        "Produit",
        "Qte",
        "Montant",
        "Etat exact",
        "Camion",
        "Chauffeur",
        "Date CDE",
        "Date etat",
    ]]

    for commande in context["commandes"]:
        data.append(
            [
                commande.reference_affichee,
                commande.current_bl,
                commande.client.entreprise,
                commande.produit.nom if commande.produit_id else "",
                str(commande.quantite or ""),
                f"{(commande.current_valorisation or Decimal('0')):,.0f}".replace(",", " "),
                commande.exact_status_label,
                commande.current_camion.numero_tracteur if commande.current_camion else "",
                commande.current_chauffeur.nom if commande.current_chauffeur else "",
                commande.date_commande.strftime("%d/%m/%Y") if commande.date_commande else "",
                commande.exact_status_date.strftime("%d/%m/%Y") if commande.exact_status_date else "",
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
    response["Content-Disposition"] = 'attachment; filename="rapport_global_commandes.pdf"'
    response.write(buffer.getvalue())
    return response


def detail_commande(request, id):
    is_admin = request.user.is_superuser
    commandes_queryset = Commande.objects.select_related(
        "client",
        "client__commercial",
        "produit",
        "camion",
        "chauffeur",
    ).prefetch_related(
        Prefetch(
            "operations",
            queryset=Operation.objects.select_related("camion", "chauffeur").order_by("-date_creation"),
        )
    )
    if get_user_role(request.user) == "commercial" and not is_admin:
        commandes_queryset = commandes_queryset.filter(client__commercial=request.user)

    commande = get_object_or_404(commandes_queryset, id=id)
    operations = list(commande.operations.all())
    latest_operation = operations[0] if operations else None
    commande.latest_operation = latest_operation
    commande.display_status = _commande_status_display(commande)
    client_payload = _client_commande_payload(commande.client)
    user_role = get_user_role(request.user)

    return render(
        request,
        "commandes/detail_commande.html",
        {
            "commande": commande,
            "operations": operations,
            "client_payload": client_payload,
            "user_role": user_role,
            "can_validate_dga": user_role == "dga" and commande.statut == "attente_validation_dga",
            "can_validate_dg": user_role == "directeur" and commande.statut == "attente_validation_dg",
        },
    )


def ajouter_commande(request):
    if request.method == "POST":
        form = CommandeForm(request.POST, user=request.user)
        if form.is_valid():
            commande = form.save(commit=False)
            commande.reference = None
            commande.statut = "attente_validation_dga"
            commande.save()
            journaliser_action(
                request.user,
                "Commandes",
                "Ajout de commande",
                _commande_label(commande),
                f"{request.user.username} a ajoute la commande {_commande_label(commande)}.",
            )
            return redirect("commandes")
    else:
        form = CommandeForm(user=request.user)

    dma_alert = None
    if form.is_bound:
        client = form.cleaned_data.get("client") if hasattr(form, "cleaned_data") else None
        quantite = form.cleaned_data.get("quantite") if hasattr(form, "cleaned_data") else None
        prix_negocie = form.cleaned_data.get("prix_negocie") if hasattr(form, "cleaned_data") else None
        if client and quantite is not None and prix_negocie is not None:
            dma_alert = _build_dma_alert(client, Decimal(quantite) * Decimal(prix_negocie))

    return render(
        request,
        "commandes/ajouter_commande.html",
        {"form": form, "clients": form.fields["client"].queryset, "dma_alert": dma_alert},
    )


def modifier_commande(request, id):
    is_admin = request.user.is_superuser
    commandes_queryset = Commande.objects.all()
    if get_user_role(request.user) == "commercial" and not is_admin:
        commandes_queryset = commandes_queryset.filter(client__commercial=request.user)
    commande = get_object_or_404(commandes_queryset, id=id)
    if not _commande_editable_before_dga(commande):
        messages.error(request, "Cette commande ne peut plus etre modifiee apres la decision du DGA.")
        return redirect("commandes")
    if request.method == "POST":
        form = CommandeForm(request.POST, instance=commande, user=request.user)
        if form.is_valid():
            commande = form.save()
            journaliser_action(
                request.user,
                "Commandes",
                "Modification de commande",
                _commande_label(commande),
                f"{request.user.username} a modifie la commande {_commande_label(commande)}.",
            )
            return redirect("commandes")
    else:
        form = CommandeForm(instance=commande, user=request.user)

    dma_alert = None
    client_for_alert = commande.client
    if form.is_bound and hasattr(form, "cleaned_data"):
        client_for_alert = form.cleaned_data.get("client") or client_for_alert
        quantite = form.cleaned_data.get("quantite")
        prix_negocie = form.cleaned_data.get("prix_negocie")
        if client_for_alert and quantite is not None and prix_negocie is not None:
            risque_actuel = client_for_alert.risque_client or Decimal("0.00")
            if commande.client_id == client_for_alert.id:
                latest_operation = (
                    commande.operations.filter(remplace_par__isnull=True).order_by("-date_creation").first()
                    or commande.operations.order_by("-date_creation").first()
                )
                commande_comptee = False
                if latest_operation:
                    commande_comptee = latest_operation.etat_bon != "livre"
                else:
                    commande_comptee = commande.statut not in {"rejetee_dg", "annulee", "livree"}
                if commande_comptee:
                    risque_actuel -= commande.montant_commande or Decimal("0.00")
            dma_alert = _build_dma_alert(
                client_for_alert,
                Decimal(quantite) * Decimal(prix_negocie),
                risque_actuel=risque_actuel,
            )
    elif commande.client_id:
        dma_alert = _build_dma_alert(commande.client)

    return render(
        request,
        "commandes/modifier_commande.html",
        {
            "form": form,
            "commande": commande,
            "clients": form.fields["client"].queryset,
            "dma_alert": dma_alert,
        },
    )


def commande_client_infos(request):
    client_id = request.GET.get("client_id")
    if not client_id:
        return JsonResponse(
            {"success": False, "errors": {"client": ["Client manquant."]}},
            status=400,
        )

    client = Client.objects.filter(id=client_id).first()
    if not client:
        return JsonResponse(
            {"success": False, "errors": {"client": ["Client introuvable."]}},
            status=404,
        )

    etat_filtre = (request.GET.get("etat") or "").strip()
    date_debut = (request.GET.get("date_debut") or "").strip()
    date_fin = (request.GET.get("date_fin") or "").strip()

    return JsonResponse(
        {
            "success": True,
            "client": _client_commande_payload(client, etat_filtre=etat_filtre, date_debut=date_debut, date_fin=date_fin),
        }
    )


def supprimer_commande(request, id):
    is_admin = request.user.is_superuser
    commandes_queryset = Commande.objects.all()
    if get_user_role(request.user) == "commercial" and not is_admin:
        commandes_queryset = commandes_queryset.filter(client__commercial=request.user)
    commande = get_object_or_404(commandes_queryset, id=id)
    commande_label = _commande_label(commande)
    commande.delete()
    journaliser_action(
        request.user,
        "Commandes",
        "Suppression de commande",
        commande_label,
        f"{request.user.username} a supprime la commande {commande_label}.",
    )
    return redirect("commandes")


def valider_commande_dga(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.decision_dga = "validee"
    commande.observation_dga = (request.POST.get("observation_dga") or "").strip()
    commande.motif_rejet_dga = ""
    commande.date_validation_dga = timezone.localdate()
    commande.statut = "attente_validation_dg"
    commande.save(
        update_fields=[
            "decision_dga",
            "observation_dga",
            "motif_rejet_dga",
            "date_validation_dga",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Commandes",
        "Validation DGA de commande",
        commande.reference,
        f"{request.user.username} a valide la commande {_commande_label(commande)} et l'a transmise au DG.",
    )
    messages.success(request, f"La commande {_commande_label(commande)} a ete transmise au DG apres validation DGA.")
    return redirect("commandes")


def rejeter_commande_dga(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.decision_dga = "rejetee"
    commande.observation_dga = (request.POST.get("observation_dga") or "").strip()
    commande.motif_rejet_dga = (request.POST.get("motif_rejet_dga") or "").strip()
    commande.date_validation_dga = timezone.localdate()
    commande.statut = "attente_validation_dg"
    commande.save(
        update_fields=[
            "decision_dga",
            "observation_dga",
            "motif_rejet_dga",
            "date_validation_dga",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Commandes",
        "Rejet DGA de commande",
        _commande_label(commande),
        f"{request.user.username} a rejete la commande {_commande_label(commande)} et l'a transmise au DG avec motif.",
    )
    messages.warning(request, f"La commande {_commande_label(commande)} a ete transmise au DG avec avis de rejet du DGA.")
    return redirect("commandes")


def valider_commande_dg(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.decision_dg = "validee"
    commande.observation_dg = (request.POST.get("observation_dg") or "").strip()
    commande.motif_rejet_dg = ""
    commande.date_validation_dg = timezone.localdate()
    commande.statut = "validee_dg"
    commande.save(
        update_fields=[
            "decision_dg",
            "observation_dg",
            "motif_rejet_dg",
            "date_validation_dg",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Commandes",
        "Validation DG de commande",
        _commande_label(commande),
        f"{request.user.username} a valide definitivement la commande {_commande_label(commande)}. Numero Sage attendu avant affectation logistique.",
    )
    messages.success(request, f"La commande {_commande_label(commande)} a ete validee par le DG. Le commercial doit maintenant renseigner le numero Sage.")
    return redirect("commandes")


def rejeter_commande_dg(request, id):
    if request.method != "POST":
        return redirect("commandes")

    commande = get_object_or_404(Commande, id=id)
    commande.decision_dg = "rejetee"
    commande.observation_dg = (request.POST.get("observation_dg") or "").strip()
    commande.motif_rejet_dg = (request.POST.get("motif_rejet_dg") or "").strip()
    commande.date_validation_dg = timezone.localdate()
    commande.statut = "rejetee_dg"
    commande.save(
        update_fields=[
            "decision_dg",
            "observation_dg",
            "motif_rejet_dg",
            "date_validation_dg",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Commandes",
        "Rejet DG de commande",
        _commande_label(commande),
        f"{request.user.username} a rejete definitivement la commande {_commande_label(commande)}.",
    )
    messages.error(request, f"La commande {_commande_label(commande)} a ete rejetee par le DG.")
    return redirect("/commandes/?scope=historique")


def renseigner_numero_commande(request, id):
    is_admin = request.user.is_superuser
    commandes_queryset = Commande.objects.all()
    user_role = get_user_role(request.user)
    if user_role == "commercial" and not is_admin:
        commandes_queryset = commandes_queryset.filter(client__commercial=request.user)
    commande = get_object_or_404(commandes_queryset, id=id)
    if commande.statut != "validee_dg":
        messages.error(request, "Le numero Sage se renseigne uniquement apres validation du DG et avant l'affectation logistique.")
        return redirect("commandes")

    if request.method == "POST":
        form = CommandeNumeroForm(request.POST, instance=commande)
        if form.is_valid():
            commande = form.save()
            journaliser_action(
                request.user,
                "Commandes",
                "Numero Sage renseigne",
                _commande_label(commande),
                f"{request.user.username} a renseigne le numero Sage {_commande_label(commande)}.",
            )
            messages.success(request, f"Le numero Sage {_commande_label(commande)} a ete enregistre. La commande peut maintenant partir en affectation logistique.")
            return redirect("commandes")
    else:
        form = CommandeNumeroForm(instance=commande)

    return render(
        request,
        "commandes/numero_commande_form.html",
        {
            "form": form,
            "commande": commande,
        },
    )


def apercu_commande_dga(request, id):
    commande = get_object_or_404(Commande.objects.select_related("client", "client__commercial", "produit"), id=id)
    return render(
        request,
        "commandes/apercu_commande_dga.html",
        {
            "commande": commande,
            "dma_alert": _build_dma_alert(commande.client),
        },
    )


def apercu_commande_dg(request, id):
    commande = get_object_or_404(Commande.objects.select_related("client", "client__commercial", "produit"), id=id)
    return render(
        request,
        "commandes/apercu_commande_dg.html",
        {
            "commande": commande,
            "dma_alert": _build_dma_alert(commande.client),
        },
    )


def affecter_commande_logistique(request, id):
    commande = get_object_or_404(
        Commande.objects.select_related("client", "produit", "camion", "chauffeur").prefetch_related("operations"),
        id=id,
    )
    if commande.statut not in {"validee_dg", "planifiee"}:
        messages.error(request, "Cette commande doit d'abord etre validee par le DG.")
        return redirect("commandes")
    if not commande.reference:
        messages.error(request, "Le numero de commande Sage doit etre renseigne avant l'affectation logistique.")
        return redirect("commandes")
    latest_locked_operation = commande.operations.filter(remplace_par__isnull=True, etat_bon="livre").order_by("-date_creation").first()
    if latest_locked_operation:
        messages.error(request, "Ce BL est deja livre. Le changement de camion n'est plus autorise.")
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
                .filter(
                    statut="validee_dg",
                    camion__isnull=True,
                    reference__isnull=False,
                    reference__gt="",
                    id__in=selected_ids,
                    produit_id=commande.produit_id,
                )
                .exclude(id=commande.id)
                .order_by("date_commande", "reference")
            )
            if len(commandes_complementaires) != len(set(selected_ids)):
                form.add_error(
                    None,
                    "Certaines commandes ajoutees ne sont plus disponibles ou n'ont pas le meme produit que la commande de base.",
                )
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
                            latest_operation = item.operations.filter(remplace_par__isnull=True).order_by("-date_creation").first()
                            if latest_operation and latest_operation.etat_bon in {"declare", "liquide", "charge", "livre"} and latest_operation.camion_id != camion.id:
                                _clone_operation_for_new_truck(latest_operation, camion, chauffeur)
                            item.camion = camion
                            item.chauffeur = chauffeur
                            item.date_affectation_logistique = today
                            item.statut = "planifiee"
                            item.save(update_fields=["camion", "chauffeur", "date_affectation_logistique", "statut"])

                    references = ", ".join(item.reference_affichee for item in commandes_complementaires)
                    journaliser_action(
                        request.user,
                        "Commandes",
                        "Affectation logistique",
                        commande.reference_affichee,
                        (
                            f"{request.user.username} a affecte le camion "
                            f"{camion.numero_tracteur} aux commandes "
                            f"{commande.reference_affichee}"
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
        .filter(
            statut="validee_dg",
            camion__isnull=True,
            reference__isnull=False,
            reference__gt="",
            produit_id=commande.produit_id,
        )
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
            "camions": form.fields["camion"].queryset.select_related("transporteur").prefetch_related("chauffeur_set"),
            "commandes_candidates": commandes_candidates_data,
            "can_manage_affretes": is_admin_user(request.user),
            "affrete_form": AffreteCreationForm() if is_admin_user(request.user) else None,
            "affrete_existant_form": AffreteCamionExistantForm() if is_admin_user(request.user) else None,
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
        Camion.objects.select_related("transporteur").prefetch_related("chauffeur_set").order_by("numero_tracteur"),
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
                "transporteur_telephone": camion.transporteur.telephone if camion.transporteur else "",
                "est_affrete": camion.est_affrete,
                "entreprise_affretee": camion.transporteur.nom if camion.est_affrete and camion.transporteur else "",
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
                    "quantite_produit": f"{int(operation.quantite) if operation.quantite == int(operation.quantite) else operation.quantite} L",
                    "produit": operation.produit.nom if operation.produit else "-",
                    "etat_bon": operation.get_etat_bon_display(),
                    "date_etat": (
                        operation.date_bons_livres.strftime("%Y-%m-%d")
                        if operation.etat_bon == "livre" and operation.date_bons_livres
                        else operation.date_bons_liquides.strftime("%Y-%m-%d")
                        if operation.etat_bon == "liquide" and operation.date_bons_liquides
                        else operation.date_bons_charges.strftime("%Y-%m-%d")
                        if operation.etat_bon == "charge" and operation.date_bons_charges
                        else operation.date_bons_declares.strftime("%Y-%m-%d")
                        if operation.etat_bon == "declare" and operation.date_bons_declares
                        else operation.date_bl.strftime("%Y-%m-%d")
                        if operation.date_bl
                        else ""
                    ),
                }
                for operation in recent_operations
            ],
        }
    )


def ajouter_affrete_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = AffreteCreationForm(request.POST)
    if form.is_valid():
        camion = form.save()
        chauffeur = camion.chauffeur_set.order_by("nom").first()
        return JsonResponse(
            {
                "success": True,
                "camion": {
                    "id": camion.id,
                    "label": (
                        f"{camion.numero_tracteur}"
                        f"{' / ' + camion.numero_citerne if camion.numero_citerne else ''}"
                        f" - {camion.capacite} L (Affrete)"
                    ),
                    "numero_tracteur": camion.numero_tracteur,
                    "numero_citerne": camion.numero_citerne or "",
                    "capacite": camion.capacite,
                    "etat": camion.etat,
                    "etat_label": camion.get_etat_display(),
                    "transporteur": camion.transporteur.nom if camion.transporteur else "",
                    "transporteur_telephone": camion.transporteur.telephone if camion.transporteur else "",
                    "est_affrete": camion.est_affrete,
                    "entreprise_affretee": camion.transporteur.nom if camion.est_affrete and camion.transporteur else "",
                    "chauffeur": {
                        "id": chauffeur.id,
                        "nom": chauffeur.nom,
                    } if chauffeur else None,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_camion_affrete_existant_modal(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "errors": {"__all__": ["Requete invalide."]}},
            status=405,
        )

    form = AffreteCamionExistantForm(request.POST)
    if form.is_valid():
        camion = form.save()
        chauffeur = camion.chauffeur_set.order_by("nom").first()
        return JsonResponse(
            {
                "success": True,
                "camion": {
                    "id": camion.id,
                    "label": (
                        f"{camion.numero_tracteur}"
                        f"{' / ' + camion.numero_citerne if camion.numero_citerne else ''}"
                        f" - {camion.capacite} L (Affrete)"
                        f"{' - ' + chauffeur.nom if chauffeur else ''}"
                    ),
                    "numero_tracteur": camion.numero_tracteur,
                    "numero_citerne": camion.numero_citerne or "",
                    "capacite": camion.capacite,
                    "etat": camion.etat,
                    "etat_label": camion.get_etat_display(),
                    "transporteur": camion.transporteur.nom if camion.transporteur else "",
                    "transporteur_telephone": camion.transporteur.telephone if camion.transporteur else "",
                    "est_affrete": camion.est_affrete,
                    "entreprise_affretee": camion.transporteur.nom if camion.est_affrete and camion.transporteur else "",
                    "chauffeur": {
                        "id": chauffeur.id,
                        "nom": chauffeur.nom,
                    } if chauffeur else None,
                },
            }
        )

    errors = {
        field: [item["message"] for item in messages]
        for field, messages in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


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
                commande.reference_affichee,
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
                commande.reference_affichee,
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


liste_commandes = role_required("commercial", "responsable_commercial", "comptable", "dga", "directeur", "logistique")(liste_commandes)
ajouter_commande = role_required("commercial", "responsable_commercial")(ajouter_commande)
detail_commande = role_required("commercial", "responsable_commercial", "comptable", "dga", "directeur", "logistique")(detail_commande)
modifier_commande = role_required("commercial", "responsable_commercial", "directeur")(modifier_commande)
renseigner_numero_commande = role_required("commercial", "responsable_commercial", "directeur")(renseigner_numero_commande)
supprimer_commande = role_required("directeur")(supprimer_commande)
valider_commande_dga = role_required("dga")(valider_commande_dga)
rejeter_commande_dga = role_required("dga")(rejeter_commande_dga)
apercu_commande_dga = role_required("dga")(apercu_commande_dga)
valider_commande_dg = role_required("directeur")(valider_commande_dg)
rejeter_commande_dg = role_required("directeur")(rejeter_commande_dg)
apercu_commande_dg = role_required("directeur")(apercu_commande_dg)
affecter_commande_logistique = role_required("logistique")(affecter_commande_logistique)
completer_capacite_commande = role_required("logistique")(completer_capacite_commande)
commande_camion_infos = role_required("logistique")(commande_camion_infos)
commande_client_infos = role_required("commercial", "responsable_commercial", "directeur")(commande_client_infos)
export_commandes_xls = role_required("commercial", "responsable_commercial", "comptable", "dga", "directeur", "logistique")(export_commandes_xls)
export_commandes_pdf = role_required("commercial", "responsable_commercial", "comptable", "dga", "directeur", "logistique")(export_commandes_pdf)
ajouter_affrete_modal = role_required()(ajouter_affrete_modal)
ajouter_camion_affrete_existant_modal = role_required()(ajouter_camion_affrete_existant_modal)
export_rapport_global_xls = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "secretaire",
    "logistique",
    "chef_chauffeur",
    "maintenancier",
    "dga",
    "dga_sogefi",
    "directeur",
    "caissiere",
    "responsable_achat",
    "comptable_sogefi",
    "transitaire",
    "invite",
    "controleur",
)(export_rapport_global_xls)
export_rapport_global_pdf = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "secretaire",
    "logistique",
    "chef_chauffeur",
    "maintenancier",
    "dga",
    "dga_sogefi",
    "directeur",
    "caissiere",
    "responsable_achat",
    "comptable_sogefi",
    "transitaire",
    "invite",
    "controleur",
)(export_rapport_global_pdf)
rapport_global = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "secretaire",
    "logistique",
    "chef_chauffeur",
    "maintenancier",
    "dga",
    "dga_sogefi",
    "directeur",
    "caissiere",
    "responsable_achat",
    "comptable_sogefi",
    "transitaire",
    "invite",
    "controleur",
)(rapport_global)
detail_rapport_global = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "secretaire",
    "logistique",
    "chef_chauffeur",
    "maintenancier",
    "dga",
    "dga_sogefi",
    "directeur",
    "caissiere",
    "responsable_achat",
    "comptable_sogefi",
    "transitaire",
    "invite",
    "controleur",
)(detail_rapport_global)
