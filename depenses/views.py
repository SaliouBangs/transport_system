from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import get_valid_filename
from django.utils import timezone

from clients.forms import BanqueForm
from clients.models import Banque
from utilisateurs.models import journaliser_action
from utilisateurs.permissions import get_active_supervision_entity, get_user_role, is_admin_user, role_required

from .forms import (
    DepenseChargementForm,
    DepenseDecisionEngagementForm,
    DepenseDecisionExpressionForm,
    DepenseEngagementForm,
    DepenseExpressionForm,
    DepensePaiementForm,
    LieuProjetForm,
    TypePieceIdentiteForm,
    TypeDepenseForm,
)
from .models import Depense, DepenseJustificatif, DepenseLigne, LieuProjet, TypeDepense, TypePieceIdentite
from operations.models import Operation


def _safe_file_response(field_file):
    if not field_file:
        raise Http404("Fichier introuvable.")
    storage = field_file.storage
    name = field_file.name
    if not name or not storage.exists(name):
        raise Http404("Fichier introuvable.")
    filename = get_valid_filename(name.split("/")[-1]) or "piece-jointe"
    return FileResponse(storage.open(name, "rb"), as_attachment=False, filename=filename)


@role_required("caissiere", "caissiere_soni", "caissiere_avena", "comptable", "comptable_sogefi", "comptable_avena", "responsable_achat", "dga", "dga_sogefi", "dga_avena", "directeur", "logistique", "admin")
def voir_piece_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), pk=id)
    justificatif = depense.pieces_justificatives.order_by("id").first()
    if justificatif:
        return _safe_file_response(justificatif.fichier)
    return _safe_file_response(depense.piece_justificative)


@role_required("caissiere", "caissiere_soni", "caissiere_avena", "comptable", "comptable_sogefi", "comptable_avena", "responsable_achat", "dga", "dga_sogefi", "dga_avena", "directeur", "logistique", "admin")
def voir_piece_depense_item(request, depense_id, piece_id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), pk=depense_id)
    justificatif = get_object_or_404(depense.pieces_justificatives.all(), pk=piece_id)
    return _safe_file_response(justificatif.fichier)


def _format_amount(amount):
    amount = Decimal(amount or 0).quantize(Decimal("0.01"))
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ")
    if text.endswith(",00"):
        return text[:-3]
    return text


def _save_depense_justificatifs(depense, files):
    uploaded_files = [item for item in files if item]
    if not uploaded_files:
        return
    first_justificatif = None
    for uploaded_file in uploaded_files:
        justificatif = DepenseJustificatif.objects.create(depense=depense, fichier=uploaded_file)
        if first_justificatif is None:
            first_justificatif = justificatif
    if not depense.piece_justificative:
        depense.piece_justificative = first_justificatif.fichier.name
        depense.save(update_fields=["piece_justificative"])


_UNITS = {
    0: "zero",
    1: "un",
    2: "deux",
    3: "trois",
    4: "quatre",
    5: "cinq",
    6: "six",
    7: "sept",
    8: "huit",
    9: "neuf",
    10: "dix",
    11: "onze",
    12: "douze",
    13: "treize",
    14: "quatorze",
    15: "quinze",
    16: "seize",
}
_TENS = {
    20: "vingt",
    30: "trente",
    40: "quarante",
    50: "cinquante",
    60: "soixante",
}


def _number_to_french(n):
    n = int(n)
    if n < 0:
        return "moins " + _number_to_french(-n)
    if n in _UNITS:
        return _UNITS[n]
    if n < 20:
        return "dix-" + _UNITS[n - 10]
    if n < 70:
        tens = (n // 10) * 10
        unit = n % 10
        base = _TENS[tens]
        if unit == 0:
            return base
        if unit == 1:
            return f"{base} et un"
        return f"{base}-{_number_to_french(unit)}"
    if n < 80:
        if n == 71:
            return "soixante et onze"
        return f"soixante-{_number_to_french(n - 60)}"
    if n < 100:
        if n == 80:
            return "quatre-vingts"
        return f"quatre-vingt-{_number_to_french(n - 80)}"
    if n < 1000:
        hundreds = n // 100
        rest = n % 100
        prefix = "cent" if hundreds == 1 else f"{_number_to_french(hundreds)} cent"
        return prefix if rest == 0 else f"{prefix} {_number_to_french(rest)}"
    if n < 1_000_000:
        thousands = n // 1000
        rest = n % 1000
        prefix = "mille" if thousands == 1 else f"{_number_to_french(thousands)} mille"
        return prefix if rest == 0 else f"{prefix} {_number_to_french(rest)}"
    millions = n // 1_000_000
    rest = n % 1_000_000
    prefix = "un million" if millions == 1 else f"{_number_to_french(millions)} millions"
    return prefix if rest == 0 else f"{prefix} {_number_to_french(rest)}"


def _amount_to_words(amount):
    amount = Decimal(amount or 0)
    entier = int(amount)
    decimals = int((amount - Decimal(entier)) * 100)
    words = _number_to_french(entier) + " francs guineens"
    if decimals:
        words += f" et {_number_to_french(decimals)} centimes"
    return words


def _parse_decimal(value, default="0"):
    raw = (str(value or default)).strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(raw or default)
    except Exception:
        return Decimal(default)


def _active_operations_for_commande(commande):
    if not commande:
        return Operation.objects.none()
    return (
        commande.operations.filter(remplace_par__isnull=True)
        .select_related("client", "camion", "chauffeur", "commande", "sommier")
        .order_by("date_creation", "id")
    )


def _commande_chargee_sur_plusieurs_bl(operation):
    if not operation.commande_id:
        return False
    return _active_operations_for_commande(operation.commande).count() > 1


def _get_chargement_scope(request, operation):
    requested_scope = (request.POST.get("portee_chargement") or request.GET.get("scope") or "").strip().lower()
    if requested_scope == Depense.PORTEE_COMMANDE and operation.commande_id:
        return Depense.PORTEE_COMMANDE
    return Depense.PORTEE_BL


def _get_scope_operations(operation, scope):
    if scope == Depense.PORTEE_COMMANDE and operation.commande_id:
        return _active_operations_for_commande(operation.commande)
    return Operation.objects.filter(id=operation.id, remplace_par__isnull=True).select_related("client", "camion", "chauffeur", "commande", "sommier")


def _build_chargement_return_url(operation_id, scope):
    suffix = "?scope=commande" if scope == Depense.PORTEE_COMMANDE else ""
    return f"/depenses/chargement/{operation_id}/ajouter/{suffix}"


def _charging_title_for_scope(operation, scope):
    if scope == Depense.PORTEE_COMMANDE and operation.commande_id:
        return f"Depense camion - commande {operation.commande.reference}"
    return f"Depense camion - BL {operation.numero_bl}"


def _charging_description_for_scope(operation, scope):
    if scope == Depense.PORTEE_COMMANDE and operation.commande_id:
        return (
            f"Depenses terrain communes rattachees a la commande {operation.commande.reference} "
            f"et partagees entre les BL issus de cette commande."
        )
    return f"Depenses terrain rattachees au BL {operation.numero_bl} pour le camion {operation.camion or '-'}."


def _scope_label(scope):
    return "Commande entiere" if scope == Depense.PORTEE_COMMANDE else "BL uniquement"


def _charge_expenses_queryset_for_operation(operation):
    base_queryset = Depense.objects.filter(source_depense=Depense.SOURCE_CHARGEMENT)
    if operation.commande_id:
        return base_queryset.filter(
            Q(operation_id=operation.id, portee_chargement=Depense.PORTEE_BL)
            | Q(commande_id=operation.commande_id, portee_chargement=Depense.PORTEE_COMMANDE)
        ).distinct()
    return base_queryset.filter(operation_id=operation.id)


def _build_ligne_values(depense, request=None):
    if request and request.method == "POST":
        designations = request.POST.getlist("ligne_designation[]")
        quantites = request.POST.getlist("ligne_quantite[]")
        prixs = request.POST.getlist("ligne_prix[]")
        lignes = []
        max_len = max(len(designations), len(quantites), len(prixs))
        for index in range(max_len):
            designation = designations[index].strip() if index < len(designations) and designations[index] else ""
            quantite = quantites[index].strip() if index < len(quantites) and quantites[index] else "1"
            prix = prixs[index].strip() if index < len(prixs) and prixs[index] else "0"
            if designation or quantite or prix:
                lignes.append(
                    {
                        "designation": designation,
                        "quantite": quantite or "1",
                        "prix_unitaire": prix or "0",
                        "montant": _format_amount(_parse_decimal(quantite or "0") * _parse_decimal(prix or "0")),
                    }
                )
        return lignes or [{"designation": "", "quantite": "1", "prix_unitaire": "0", "montant": "0"}]

    if depense.pk and depense.lignes.exists():
        return [
            {
                "designation": ligne.designation,
                "quantite": str(ligne.quantite),
                "prix_unitaire": str(ligne.prix_unitaire),
                "montant": _format_amount(ligne.montant),
            }
            for ligne in depense.lignes.all()
        ]
    return [{"designation": "", "quantite": "1", "prix_unitaire": "0", "montant": "0"}]


def _validate_lignes(request):
    designations = request.POST.getlist("ligne_designation[]")
    quantites = request.POST.getlist("ligne_quantite[]")
    prixs = request.POST.getlist("ligne_prix[]")
    lignes = []
    errors = []
    max_len = max(len(designations), len(quantites), len(prixs))
    for index in range(max_len):
        designation = designations[index].strip() if index < len(designations) and designations[index] else ""
        quantite_raw = quantites[index].strip() if index < len(quantites) and quantites[index] else ""
        prix_raw = prixs[index].strip() if index < len(prixs) and prixs[index] else ""
        if not designation and not quantite_raw and not prix_raw:
            continue
        quantite = _parse_decimal(quantite_raw or "0")
        prix = _parse_decimal(prix_raw or "0")
        if not designation:
            errors.append(f"Ligne {index + 1}: la designation est obligatoire.")
            continue
        if quantite <= 0:
            errors.append(f"Ligne {index + 1}: la quantite doit etre superieure a zero.")
            continue
        if prix < 0:
            errors.append(f"Ligne {index + 1}: le prix unitaire ne peut pas etre negatif.")
            continue
        lignes.append(
            {
                "designation": designation,
                "quantite": quantite,
                "prix_unitaire": prix,
                "montant": quantite * prix,
            }
        )
    if not lignes:
        errors.append("Ajoutez au moins une ligne de depense.")
    return lignes, errors


def _save_depense_lignes(depense, lignes):
    depense.lignes.all().delete()
    total = Decimal("0")
    for ligne in lignes:
        DepenseLigne.objects.create(
            depense=depense,
            designation=ligne["designation"],
            quantite=ligne["quantite"],
            prix_unitaire=ligne["prix_unitaire"],
        )
        total += ligne["montant"]
    depense.montant_engage = total
    depense.save(update_fields=["montant_engage"])


def _build_expression_ligne_values(depense, request=None):
    if request and request.method == "POST":
        designations = request.POST.getlist("ligne_designation[]")
        quantites = request.POST.getlist("ligne_quantite[]")
        lignes = []
        max_len = max(len(designations), len(quantites))
        for index in range(max_len):
            designation = designations[index].strip() if index < len(designations) and designations[index] else ""
            quantite = quantites[index].strip() if index < len(quantites) and quantites[index] else "1"
            if designation or quantite:
                lignes.append(
                    {
                        "designation": designation,
                        "quantite": quantite or "1",
                    }
                )
        return lignes or [{"designation": "", "quantite": "1"}]

    if depense and depense.pk and depense.lignes.exists():
        return [
            {
                "designation": ligne.designation,
                "quantite": str(ligne.quantite),
            }
            for ligne in depense.lignes.all()
        ]
    return [{"designation": "", "quantite": "1"}]


def _validate_expression_lignes(request):
    designations = request.POST.getlist("ligne_designation[]")
    quantites = request.POST.getlist("ligne_quantite[]")
    lignes = []
    errors = []
    max_len = max(len(designations), len(quantites))
    for index in range(max_len):
        designation = designations[index].strip() if index < len(designations) and designations[index] else ""
        quantite_raw = quantites[index].strip() if index < len(quantites) and quantites[index] else ""
        if not designation and not quantite_raw:
            continue
        quantite = _parse_decimal(quantite_raw or "0")
        if not designation:
            errors.append(f"Ligne {index + 1}: le libelle est obligatoire.")
            continue
        if quantite <= 0:
            errors.append(f"Ligne {index + 1}: la quantite doit etre superieure a zero.")
            continue
        lignes.append(
            {
                "designation": designation,
                "quantite": quantite,
                "prix_unitaire": Decimal("0"),
                "montant": Decimal("0"),
            }
        )
    if not lignes:
        errors.append("Ajoutez au moins une ligne de besoin.")
    return lignes, errors


def _decision_expression_allowed(user):
    return is_admin_user(user) or get_user_role(user) in {"dga_sogefi", "dga_avena", "directeur"}


def _decision_engagement_dga_allowed(user):
    return is_admin_user(user) or get_user_role(user) in {"dga", "dga_sogefi", "dga_avena"}


def _decision_engagement_dg_allowed(user):
    return is_admin_user(user) or get_user_role(user) == "directeur"


def _engagement_allowed(user):
    return is_admin_user(user) or get_user_role(user) in {"responsable_achat", "logistique", "comptable_avena"}


def _internal_expression_creation_allowed(user):
    return is_admin_user(user) or get_user_role(user) in {"dga_sogefi", "responsable_achat", "logistique", "dga_avena", "directeur"}


def _type_depense_management_allowed(user):
    return is_admin_user(user) or get_user_role(user) in {"dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena", "directeur"}


def _internal_entity_for_user(user):
    role = get_user_role(user)
    if role in {"dga_sogefi", "responsable_achat", "comptable_sogefi", "caissiere"}:
        return Depense.ENTITE_SOGEFI
    if role in {"logistique", "dga", "comptable", "caissiere_soni"}:
        return Depense.ENTITE_SONI
    if role in {"dga_avena", "comptable_avena", "caissiere_avena"}:
        return Depense.ENTITE_AVENA
    return None


def _internal_entity_label(entity_code):
    if entity_code == Depense.ENTITE_SONI:
        return "SONI"
    if entity_code == Depense.ENTITE_AVENA:
        return "Avena"
    return "SOGEFI"


def _reference_entity_for_user(user):
    entity = _internal_entity_for_user(user)
    return entity or Depense.ENTITE_SOGEFI


def _internal_dga_label(depense):
    if depense.entite_depense == Depense.ENTITE_SONI:
        return "DGA SONI"
    if depense.entite_depense == Depense.ENTITE_AVENA:
        return "DGA Avena"
    return "DGA SOGEFI"


def _internal_cashier_label(depense):
    if depense.entite_depense == Depense.ENTITE_SONI:
        return "Caissiere SONI"
    if depense.entite_depense == Depense.ENTITE_AVENA:
        return "Caissiere Avena"
    return "Caissiere"


def _internal_dga_role(depense):
    if depense.entite_depense == Depense.ENTITE_SONI:
        return "dga"
    if depense.entite_depense == Depense.ENTITE_AVENA:
        return "dga_avena"
    return "dga_sogefi"


def _type_depense_portefeuille_for_role(user, fallback=None):
    role = get_user_role(user)
    if role in {"dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena"}:
        return TypeDepense.PORTEFEUILLE_INTERNE
    if role == "logistique":
        return TypeDepense.PORTEFEUILLE_LOGISTIQUE
    return fallback or TypeDepense.PORTEFEUILLE_LOGISTIQUE


def _types_depense_queryset_for_user(user):
    portefeuille = _type_depense_portefeuille_for_role(user, fallback=None)
    queryset = TypeDepense.objects.order_by("libelle")
    if portefeuille:
        queryset = queryset.filter(portefeuille=portefeuille)
    if portefeuille == TypeDepense.PORTEFEUILLE_INTERNE:
        queryset = queryset.filter(entite_reference=_reference_entity_for_user(user))
    return queryset


def _paiement_allowed(user, depense):
    role = get_user_role(user)
    if is_admin_user(user):
        return True
    if depense.mode_reglement == Depense.MODE_CHEQUE:
        if depense.source_depense == Depense.SOURCE_CHARGEMENT:
            return role == "comptable_sogefi"
        if depense.entite_depense == Depense.ENTITE_SONI:
            return role == "comptable"
        if depense.entite_depense == Depense.ENTITE_AVENA:
            return role == "comptable_avena"
        return role == "comptable_sogefi"
    if depense.mode_reglement == Depense.MODE_ESPECE:
        if depense.source_depense == Depense.SOURCE_CHARGEMENT:
            return role == "caissiere"
        if depense.entite_depense == Depense.ENTITE_SONI:
            return role == "caissiere_soni"
        if depense.entite_depense == Depense.ENTITE_AVENA:
            return role == "caissiere_avena"
        return role == "caissiere"
    return False


def _is_charge_related(depense):
    return depense.source_depense == Depense.SOURCE_CHARGEMENT


def _chargement_dga_pending(depense):
    return depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA


def _chargement_dg_pending(depense):
    return (
        depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
        and depense.expression_decidee_par_dga()
    )


def _chargement_editable_by_logistique(depense, user):
    return (
        get_user_role(user) == "logistique"
        and depense.source_depense == Depense.SOURCE_CHARGEMENT
        and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
        and not depense.expression_decision_dga
    )


def _depenses_chargement_lot(depense):
    if depense.portee_chargement == Depense.PORTEE_COMMANDE and depense.commande_id:
        return (
            Depense.objects.filter(
                commande_id=depense.commande_id,
                source_depense=Depense.SOURCE_CHARGEMENT,
                portee_chargement=Depense.PORTEE_COMMANDE,
            )
            .select_related("type_depense", "demandeur", "operation", "commande")
            .order_by("date_creation", "id")
        )
    if not depense.operation_id:
        return Depense.objects.none()
    return (
        Depense.objects.filter(
            operation_id=depense.operation_id,
            source_depense=Depense.SOURCE_CHARGEMENT,
            portee_chargement=Depense.PORTEE_BL,
        )
        .select_related("type_depense", "demandeur", "operation", "commande")
        .order_by("date_creation", "id")
    )


def _depense_chargement_maitre(operation, scope=Depense.PORTEE_BL):
    if scope == Depense.PORTEE_COMMANDE and operation.commande_id:
        return (
            Depense.objects.filter(
                source_depense=Depense.SOURCE_CHARGEMENT,
                portee_chargement=Depense.PORTEE_COMMANDE,
                commande_id=operation.commande_id,
            )
            .order_by("date_creation", "id")
            .first()
        )
    return (
        operation.depenses_liees.filter(
            source_depense=Depense.SOURCE_CHARGEMENT,
            portee_chargement=Depense.PORTEE_BL,
        )
        .order_by("date_creation", "id")
        .first()
    )


def _depense_has_carburant_line(depense):
    if depense.est_depense_carburant():
        return True
    return depense.lignes.filter(type_depense__libelle__iregex=r"(carburant|gasoil|essence)").exists()


def _sync_depense_chargement_totals(depense):
    total = depense.lignes.aggregate(total=Sum("montant"))["total"] or Decimal("0")
    depense.montant_estime = total
    depense.save(update_fields=["montant_estime", "date_mise_a_jour"])


def _depenses_queryset_for_user(user):
    role = get_user_role(user)
    queryset = Depense.objects.select_related(
        "demandeur",
        "type_depense",
        "lieu_projet_ref",
        "fournisseur",
        "commande",
        "operation",
        "expression_validee_par",
        "expression_decision_dga_par",
        "expression_decision_dg_par",
        "engagement_saisi_par",
        "validation_dga_par",
        "validation_dg_par",
        "paiement_saisi_par",
    )
    if role == "caissiere":
        return queryset.filter(
            Q(statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE, source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SOGEFI,
            )
            | Q(
                statut=Depense.STATUT_PAYEE,
                mode_reglement=Depense.MODE_ESPECE,
                paiement_saisi_par=user,
            )
        )
    if role == "caissiere_soni":
        return queryset.filter(
            Q(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SONI,
            )
            | Q(
                statut=Depense.STATUT_PAYEE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SONI,
                mode_reglement=Depense.MODE_ESPECE,
                paiement_saisi_par=user,
            )
        )
    if role == "caissiere_avena":
        return queryset.filter(
            Q(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_AVENA,
            )
            | Q(
                statut=Depense.STATUT_PAYEE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_AVENA,
                mode_reglement=Depense.MODE_ESPECE,
                paiement_saisi_par=user,
            )
        )
    if role in {"dga_sogefi", "responsable_achat"}:
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SOGEFI)
    if role == "dga":
        return queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
        )
    if role == "logistique":
        return queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
        )
    if role == "comptable":
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
    if role == "dga_avena":
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_AVENA)
    if role == "comptable_avena":
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_AVENA)
    if role == "comptable_sogefi":
        return queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SOGEFI)
        )
    if is_admin_user(user) or role in {
        "directeur",
        "responsable_achat",
        "controleur",
    }:
        return queryset
    return queryset.filter(demandeur=user)


def _apply_supervision_entity_filter(request, queryset):
    entity = get_active_supervision_entity(request)
    if not entity or entity == "all":
        return queryset
    if entity == Depense.ENTITE_SONI:
        return queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
        )
    if entity == Depense.ENTITE_SOGEFI:
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SOGEFI)
    if entity == Depense.ENTITE_AVENA:
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_AVENA)
    return queryset


def _summary_counts(queryset):
    return {
        "total": queryset.count(),
        "attente_expression": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION).count(),
        "attente_expression_dg": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG).count(),
        "attente_chargement_dga": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA).count(),
        "attente_chargement_dg": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG).count(),
        "attente_engagement": queryset.filter(statut=Depense.STATUT_ATTENTE_ENGAGEMENT).count(),
        "attente_dga": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DGA).count(),
        "attente_dg": queryset.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DG).count(),
        "attente_cheque": queryset.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE).count(),
        "attente_espece": queryset.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE).count(),
        "payees": queryset.filter(statut=Depense.STATUT_PAYEE).count(),
    }


def _build_context(user, queryset):
    role = get_user_role(user)
    internal_entity = _internal_entity_for_user(user)
    return {
        "user_role": role,
        "internal_depense_scope": role in {"dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena"},
        "internal_entity_label": _internal_entity_label(internal_entity) if internal_entity else "",
        "dga_sogefi_scope": role == "dga_sogefi",
        "counts": _summary_counts(queryset),
        "can_validate_expression": _decision_expression_allowed(user),
        "can_validate_chargement_dga": is_admin_user(user) or role == "dga",
        "can_manage_engagement": _engagement_allowed(user),
        "can_validate_dga": _decision_engagement_dga_allowed(user),
        "can_validate_dg": _decision_engagement_dg_allowed(user),
        "can_access_paiement": role in {"comptable", "comptable_sogefi", "comptable_avena", "caissiere", "caissiere_soni", "caissiere_avena"} or is_admin_user(user),
        "can_create_internal_depense": _internal_expression_creation_allowed(user),
    }


def _build_internal_report_queryset(request):
    queryset = _depenses_queryset_for_user(request.user).filter(source_depense=Depense.SOURCE_GENERALE)
    internal_entity = _internal_entity_for_user(request.user)
    if internal_entity:
        queryset = queryset.filter(entite_depense=internal_entity)
    q = (request.GET.get("q") or "").strip()
    statut = (request.GET.get("statut") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    if q:
        queryset = queryset.filter(
            Q(reference__icontains=q)
            | Q(titre__icontains=q)
            | Q(description__icontains=q)
            | Q(demandeur__username__icontains=q)
            | Q(fournisseur__nom_fournisseur__icontains=q)
            | Q(fournisseur__entreprise__icontains=q)
            | Q(type_depense__libelle__icontains=q)
            | Q(lieu_ou_projet__icontains=q)
            | Q(numero_facture__icontains=q)
        )
    if statut:
        queryset = queryset.filter(statut=statut)
    if date_from:
        queryset = queryset.filter(date_expression__gte=date_from)
    if date_to:
        queryset = queryset.filter(date_expression__lte=date_to)

    return queryset.order_by("-date_expression", "-date_creation"), {
        "q": q,
        "statut": statut,
        "date_from": date_from,
        "date_to": date_to,
    }


def _build_internal_report_context(request):
    queryset, filters = _build_internal_report_queryset(request)
    depenses = list(queryset)
    total_montant = Decimal("0")
    total_quantite = Decimal("0")

    for depense in depenses:
        depense.date_expression_affichee = depense.date_expression.strftime("%d/%m/%Y") if depense.date_expression else "-"
        depense.montant_affiche = _format_amount(depense.montant_comptable)
        depense.quantite_affichee = _format_amount(depense.quantite_totale)
        depense.type_affiche = str(depense.type_depense) if depense.type_depense_id else "-"
        depense.fournisseur_affiche = str(depense.fournisseur) if depense.fournisseur_id else "-"
        depense.lieu_affiche = depense.lieu_ou_projet or (str(depense.lieu_projet_ref) if depense.lieu_projet_ref_id else "-")
        depense.mode_affiche = depense.get_mode_reglement_display() if depense.mode_reglement else "-"
        total_montant += depense.montant_comptable or Decimal("0")
        total_quantite += depense.quantite_totale or Decimal("0")

    current_filters = urlencode({key: value for key, value in filters.items() if value})

    return {
        "depenses": depenses,
        "counts": _summary_counts(queryset),
        "total_montant_affiche": _format_amount(total_montant),
        "total_quantite_affichee": _format_amount(total_quantite),
        "search_query": filters["q"],
        "statut_filter": filters["statut"],
        "date_from": filters["date_from"],
        "date_to": filters["date_to"],
        "statut_choices": Depense.STATUT_CHOICES,
        "current_filters": current_filters,
        **_build_context(request.user, queryset),
    }


def _internal_report_export_rows(queryset):
    rows = []
    for depense in queryset:
        rows.append(
            [
                depense.reference,
                depense.date_expression.strftime("%Y-%m-%d") if depense.date_expression else "",
                depense.demandeur.username if depense.demandeur_id else "",
                depense.titre,
                str(depense.type_depense) if depense.type_depense_id else "",
                depense.lieu_ou_projet or (str(depense.lieu_projet_ref) if depense.lieu_projet_ref_id else ""),
                str(depense.fournisseur) if depense.fournisseur_id else "",
                depense.numero_facture or "",
                float(depense.quantite_totale) if depense.quantite_totale is not None else "",
                float(depense.montant_comptable) if depense.montant_comptable is not None else "",
                depense.get_statut_display(),
                depense.get_mode_reglement_display() if depense.mode_reglement else "",
                depense.date_paiement.strftime("%Y-%m-%d") if depense.date_paiement else "",
            ]
        )
    return rows


def rapport_depenses_internes(request):
    return render(
        request,
        "depenses/rapport_depenses_internes.html",
        _build_internal_report_context(request),
    )


def export_rapport_depenses_internes_xls(request):
    try:
        from openpyxl import Workbook
    except ImportError:
        return HttpResponse(
            "Le module openpyxl n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    queryset, _filters = _build_internal_report_queryset(request)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Achats internes"
    sheet.append(
        [
            "Reference",
            "Date expression",
            "Demandeur",
            "Titre",
            "Type de depense",
            "Lieu / projet",
            "Fournisseur",
            "Numero facture",
            "Quantite",
            "Montant",
            "Statut",
            "Mode DG",
            "Date paiement",
        ]
    )
    for row in _internal_report_export_rows(queryset):
        sheet.append(row)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="rapport_achats_internes.xlsx"'
    workbook.save(response)
    return response


def export_rapport_depenses_internes_pdf(request):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError:
        return HttpResponse(
            "Le module reportlab n'est pas installe sur cet environnement Python.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    queryset, filters = _build_internal_report_queryset(request)
    total_montant = sum(((depense.montant_comptable or Decimal("0")) for depense in queryset), Decimal("0"))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    title_style = styles["Heading2"]
    title_style.textColor = colors.HexColor("#123047")
    info_style = styles["BodyText"]
    data = [[
        "Reference",
        "Date",
        "Titre",
        "Type",
        "Fournisseur",
        "Facture",
        "Qte",
        "Montant",
        "Statut",
    ]]
    for depense in queryset:
        data.append(
            [
                depense.reference,
                depense.date_expression.strftime("%d/%m/%Y") if depense.date_expression else "",
                depense.titre,
                str(depense.type_depense) if depense.type_depense_id else "",
                str(depense.fournisseur) if depense.fournisseur_id else "",
                depense.numero_facture or "",
                _format_amount(depense.quantite_totale),
                _format_amount(depense.montant_comptable),
                depense.get_statut_display(),
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
    filters_parts = []
    if filters.get("date_from") or filters.get("date_to"):
        filters_parts.append(
            f"Periode : {filters.get('date_from') or '-'} au {filters.get('date_to') or '-'}"
        )
    if filters.get("statut"):
        filters_parts.append(f"Statut : {dict(Depense.STATUT_CHOICES).get(filters['statut'], filters['statut'])}")
    if filters.get("q"):
        filters_parts.append(f"Recherche : {filters['q']}")

    elements = [
        Paragraph("Rapport des achats internes", title_style),
        Spacer(1, 8),
        Paragraph(f"Montant total filtre : <b>{_format_amount(total_montant)} GNF</b>", info_style),
    ]
    if filters_parts:
        elements.append(Paragraph(" | ".join(filters_parts), info_style))
    elements.extend([Spacer(1, 10), table])
    doc.build(elements)

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="rapport_achats_internes.pdf"'
    response.write(buffer.getvalue())
    return response


def _build_preview_context(depense):
    lignes = list(depense.lignes.all())
    lot_depenses = []
    montant_total_affiche = _format_amount(depense.montant_comptable)
    quantite_totale_affiche = _format_amount(depense.quantite_totale)
    montant_en_lettres = _amount_to_words(depense.montant_comptable)

    if _is_charge_related(depense):
        lot_depenses = list(_depenses_chargement_lot(depense))
        if depense.lignes.exists():
            lignes = list(depense.lignes.all())
            for ligne in lignes:
                ligne.quantite_affiche = _format_amount(ligne.quantite)
                ligne.prix_unitaire_affiche = _format_amount(ligne.prix_unitaire)
                ligne.montant_affiche = _format_amount(ligne.montant)
            montant_total_affiche = _format_amount(depense.montant_comptable)
            quantite_totale_affiche = _format_amount(depense.quantite_totale)
            montant_en_lettres = _amount_to_words(depense.montant_comptable)
        else:
            lignes = []
            lot_total = Decimal("0")
            for depense_lot in lot_depenses:
                lot_total += depense_lot.montant_comptable or Decimal("0")
                lignes.append(
                    type(
                        "LigneVirtuelle",
                        (),
                        {
                            "designation": depense_lot.type_depense.libelle if depense_lot.type_depense_id else (depense_lot.libelle_depense or depense_lot.titre),
                            "quantite_affiche": _format_amount(depense_lot.quantite_a_consommer) if depense_lot.est_depense_carburant() and depense_lot.quantite_a_consommer not in (None, "") else _format_amount(Decimal("1")),
                            "prix_unitaire_affiche": _format_amount(Decimal("12000")) if depense_lot.est_depense_carburant() else _format_amount(depense_lot.montant_estime or 0),
                            "montant_affiche": _format_amount(depense_lot.montant_estime or 0),
                            "commentaire": depense_lot.description or "-",
                            "reference": depense_lot.reference,
                        },
                    )()
                )
            montant_total_affiche = _format_amount(lot_total)
            quantite_totale_affiche = _format_amount(len(lot_depenses))
            montant_en_lettres = _amount_to_words(lot_total)
    elif not lignes:
        lignes = []
    for ligne in lignes:
        if not hasattr(ligne, "quantite_affiche"):
            ligne.quantite_affiche = _format_amount(ligne.quantite)
            ligne.prix_unitaire_affiche = _format_amount(ligne.prix_unitaire)
            ligne.montant_affiche = _format_amount(ligne.montant)
    if _is_charge_related(depense):
        validation_steps = [
            {
                "label": "Validation DGA",
                "status": depense.get_expression_decision_dga_display() if depense.expression_decision_dga else "En attente",
                "variant": "ok" if depense.expression_decision_dga == Depense.DECISION_VALIDEE else "danger" if depense.expression_decision_dga == Depense.DECISION_REJETEE else "pending",
                "by": depense.expression_decision_dga_par.username if depense.expression_decision_dga_par else "-",
                "at": depense.expression_decision_dga_le,
                "motif": depense.expression_decision_dga_motif or "",
            },
            {
                "label": "Validation DG",
                "status": depense.get_expression_decision_dg_display() if depense.expression_decision_dg else "En attente",
                "variant": "ok" if depense.expression_decision_dg == Depense.DECISION_VALIDEE else "danger" if depense.expression_decision_dg == Depense.DECISION_REJETEE else "pending",
                "by": depense.expression_decision_dg_par.username if depense.expression_decision_dg_par else "-",
                "at": depense.expression_decision_dg_le,
                "motif": depense.expression_decision_dg_motif or "",
            },
            {
                "label": "Paiement caissiere",
                "status": "Paiement effectue" if depense.statut == Depense.STATUT_PAYEE else "En attente",
                "variant": "ok" if depense.statut == Depense.STATUT_PAYEE else "pending",
                "by": depense.paiement_saisi_par.username if depense.paiement_saisi_par else "-",
                "at": depense.paiement_saisi_le,
                "motif": depense.paiement_observation or "",
            },
        ]
    else:
        validation_steps = [
            {
                "label": "Saisie achat / prix",
                "status": "Engagement saisi" if depense.engagement_saisi_le else "En attente de saisie achat",
                "variant": "ok" if depense.engagement_saisi_le else "pending",
                "by": depense.engagement_saisi_par.username if depense.engagement_saisi_par else "-",
                "at": depense.engagement_saisi_le,
                "motif": depense.engagement_observation or "",
            },
            {
                "label": "Engagement DGA SOGEFI",
                "status": depense.get_engagement_decision_dga_display() if depense.engagement_decision_dga else "En attente",
                "variant": "ok" if depense.engagement_decision_dga == Depense.DECISION_VALIDEE else "danger" if depense.engagement_decision_dga == Depense.DECISION_REJETEE else "pending",
                "by": depense.validation_dga_par.username if depense.validation_dga_par else "-",
                "at": depense.validation_dga_le,
                "motif": depense.motif_rejet_dga or "",
            },
            {
                "label": "Engagement DG",
                "status": depense.get_engagement_decision_dg_display() if depense.engagement_decision_dg else "En attente",
                "variant": "ok" if depense.engagement_decision_dg == Depense.DECISION_VALIDEE else "danger" if depense.engagement_decision_dg == Depense.DECISION_REJETEE else "pending",
                "by": depense.validation_dg_par.username if depense.validation_dg_par else "-",
                "at": depense.validation_dg_le,
                "motif": depense.motif_rejet_dg or "",
            },
            {
                "label": "Paiement",
                "status": "Paiement effectue" if depense.statut == Depense.STATUT_PAYEE else "En attente",
                "variant": "ok" if depense.statut == Depense.STATUT_PAYEE else "pending",
                "by": depense.paiement_saisi_par.username if depense.paiement_saisi_par else "-",
                "at": depense.paiement_saisi_le,
                "motif": depense.paiement_observation or "",
            },
        ]
    return {
        "depense": depense,
        "lignes": lignes,
        "lot_depenses": lot_depenses,
        "montant_total_affiche": montant_total_affiche,
        "quantite_totale_affiche": quantite_totale_affiche,
        "montant_en_lettres": montant_en_lettres,
        "validation_steps": validation_steps,
        "is_charge_related": _is_charge_related(depense),
        "internal_dga_label": _internal_dga_label(depense),
        "internal_cashier_label": _internal_cashier_label(depense),
        "internal_entity_label": _internal_entity_label(depense.entite_depense),
        "is_fuel_related": depense.est_depense_carburant(),
        "can_print_bon_conso": _depense_has_carburant_line(depense),
        "return_url": f"/operations/logisticien/{depense.operation_id}/modifier/" if depense.source_depense == Depense.SOURCE_CHARGEMENT and depense.operation_id else "/depenses/",
    }


def liste_depenses(request):
    queryset = _apply_supervision_entity_filter(request, _depenses_queryset_for_user(request.user))
    q = (request.GET.get("q") or "").strip()
    statut = (request.GET.get("statut") or "").strip()
    entreprise = (request.GET.get("entreprise") or "").strip()
    dg_global_statut = "attente_dg_global"
    dg_global_statuses = [
        Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG,
        Depense.STATUT_ATTENTE_VALIDATION_DG,
        Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG,
    ]
    if q:
        queryset = queryset.filter(
            Q(reference__icontains=q)
            | Q(titre__icontains=q)
            | Q(description__icontains=q)
            | Q(demandeur__username__icontains=q)
            | Q(fournisseur__nom_fournisseur__icontains=q)
            | Q(fournisseur__entreprise__icontains=q)
            | Q(operation__numero_bl__icontains=q)
        )
    if statut:
        if statut == dg_global_statut:
            queryset = queryset.filter(statut__in=dg_global_statuses)
        else:
            queryset = queryset.filter(statut=statut)
    if entreprise == Depense.ENTITE_SOGEFI:
        queryset = queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SOGEFI)
        )
    elif entreprise in {Depense.ENTITE_SONI, Depense.ENTITE_AVENA}:
        queryset = queryset.filter(
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=entreprise,
        )

    depenses = []
    seen_chargement_operations = set()
    for depense in queryset.order_by("-date_creation"):
        if depense.source_depense == Depense.SOURCE_CHARGEMENT and depense.operation_id:
            if depense.operation_id in seen_chargement_operations:
                continue
            seen_chargement_operations.add(depense.operation_id)
            depense = _depense_chargement_maitre(depense.operation) or depense
            depense.type_affiche = "Depenses camion"
            depense.entreprise_label = "SOGEFI"
        else:
            depense.type_affiche = str(depense.type_depense) if depense.type_depense_id else "-"
            depense.internal_dga_label = _internal_dga_label(depense)
            depense.entreprise_label = _internal_entity_label(depense.entite_depense)
        depense.montant_affiche = _format_amount(depense.montant_comptable)
        depenses.append(depense)

    return render(
        request,
        "depenses/liste_depenses.html",
        {
            "depenses": depenses,
            "statut_filter": statut,
            "entreprise_filter": entreprise,
            "entreprise_choices": [
                (Depense.ENTITE_SOGEFI, "SOGEFI"),
                (Depense.ENTITE_SONI, "SONI"),
                (Depense.ENTITE_AVENA, "Avena"),
            ],
            "search_query": q,
            "statut_choices": [*Depense.STATUT_CHOICES, (dg_global_statut, "Toutes les attentes DG")],
            **_build_context(request.user, queryset),
        },
    )


def liste_types_depense(request):
    if not _type_depense_management_allowed(request.user):
        messages.error(request, "Vous n'avez pas acces a la gestion des types de depense.")
        return redirect("liste_depenses")

    portefeuille = _type_depense_portefeuille_for_role(request.user)
    form = TypeDepenseForm(request.POST or None, portefeuille=portefeuille, entite_reference=_reference_entity_for_user(request.user))
    if request.method == "POST" and form.is_valid():
        type_depense = form.save()
        messages.success(request, f"Le type de depense {type_depense.libelle} a ete ajoute.")
        return redirect("liste_types_depense")

    types_depense = list(_types_depense_queryset_for_user(request.user))
    for type_depense in types_depense:
        type_depense.nb_depenses = type_depense.depenses.count()
        type_depense.montant_defaut_affiche = _format_amount(type_depense.montant_defaut)

    return render(
        request,
        "depenses/types_depense.html",
        {
            "form": form,
            "types_depense": types_depense,
            "portefeuille_type_depense": portefeuille,
        },
    )


def modifier_type_depense(request, id):
    if not _type_depense_management_allowed(request.user):
        messages.error(request, "Vous n'avez pas acces a la gestion des types de depense.")
        return redirect("liste_depenses")

    portefeuille = _type_depense_portefeuille_for_role(request.user)
    type_depense = get_object_or_404(_types_depense_queryset_for_user(request.user), id=id)
    form = TypeDepenseForm(request.POST or None, instance=type_depense, portefeuille=portefeuille, entite_reference=_reference_entity_for_user(request.user))
    if request.method == "POST" and form.is_valid():
        type_depense = form.save()
        messages.success(request, f"Le type de depense {type_depense.libelle} a ete mis a jour.")
        return redirect("liste_types_depense")

    types_depense = list(_types_depense_queryset_for_user(request.user))
    for item in types_depense:
        item.nb_depenses = item.depenses.count()
        item.montant_defaut_affiche = _format_amount(item.montant_defaut)

    return render(
        request,
        "depenses/types_depense.html",
        {
            "form": form,
            "type_en_cours": type_depense,
            "types_depense": types_depense,
            "portefeuille_type_depense": portefeuille,
        },
    )


def supprimer_type_depense(request, id):
    if not is_admin_user(request.user):
        messages.error(request, "Seul l'administrateur peut gerer les types de depense.")
        return redirect("liste_depenses")
    if request.method != "POST":
        return redirect("liste_types_depense")

    type_depense = get_object_or_404(_types_depense_queryset_for_user(request.user), id=id)
    if type_depense.depenses.exists():
        messages.error(request, "Impossible de supprimer ce type, il est deja utilise dans des depenses.")
        return redirect("liste_types_depense")

    libelle = type_depense.libelle
    type_depense.delete()
    messages.success(request, f"Le type de depense {libelle} a ete supprime.")
    return redirect("liste_types_depense")


def ajouter_depense(request):
    if not _internal_expression_creation_allowed(request.user):
        messages.error(request, "Seuls les profils autorises peuvent creer une depense interne.")
        return redirect("liste_depenses")
    if request.method == "POST":
        form = DepenseExpressionForm(request.POST)
        ligne_values = _build_expression_ligne_values(None, request=request)
        if form.is_valid():
            lignes, ligne_errors = _validate_expression_lignes(request)
            if ligne_errors:
                for error in ligne_errors:
                    form.add_error(None, error)
            else:
                depense = form.save(commit=False)
                depense.demandeur = request.user
                depense.source_depense = Depense.SOURCE_GENERALE
                depense.entite_depense = _internal_entity_for_user(request.user) or Depense.ENTITE_SOGEFI
                depense.statut = Depense.STATUT_ATTENTE_ENGAGEMENT
                depense.save()
                _save_depense_lignes(depense, lignes)
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Creation depense interne",
                    depense.reference,
                    f"{request.user.username} a cree la depense interne {depense.reference}.",
                )
                messages.success(request, "La depense interne a ete creee et transmise pour la saisie des prix.")
                return redirect("liste_depenses")
    else:
        form = DepenseExpressionForm()
        ligne_values = _build_expression_ligne_values(None)

    return render(
        request,
        "depenses/form_expression.html",
        {
            "form": form,
            "page_title": "Nouvelle depense interne",
            "is_edit": False,
            "ligne_values": ligne_values,
        },
    )


def modifier_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    can_edit = (
        depense.source_depense != Depense.SOURCE_GENERALE
        and depense.demandeur_id == request.user.id
        and depense.statut in {Depense.STATUT_BROUILLON, Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION}
    ) or (
        depense.source_depense == Depense.SOURCE_GENERALE
        and depense.demandeur_id == request.user.id
        and depense.statut == Depense.STATUT_ATTENTE_ENGAGEMENT
    ) or is_admin_user(request.user) or _chargement_editable_by_logistique(depense, request.user)
    if not can_edit:
        messages.error(request, "Cette expression ne peut plus etre modifiee.")
        return redirect("liste_depenses")

    if depense.source_depense == Depense.SOURCE_CHARGEMENT:
        form_class = DepenseChargementForm
        template_name = "depenses/form_chargement.html"
        page_title = f"Modifier {depense.reference}"
        success_message = "La depense camion a ete mise a jour."
        context_extra = {
            "operation": depense.operation,
            "type_depense_form": TypeDepenseForm(portefeuille=TypeDepense.PORTEFEUILLE_LOGISTIQUE),
            "portee_chargement": depense.portee_chargement,
            "scope_label": _scope_label(depense.portee_chargement),
            "scope_operations": list(_get_scope_operations(depense.operation, depense.portee_chargement)) if depense.operation_id else [],
            "commande_multi_bl": bool(depense.operation_id and _commande_chargee_sur_plusieurs_bl(depense.operation)),
        }
    else:
        form_class = DepenseExpressionForm
        template_name = "depenses/form_expression.html"
        page_title = f"Modifier {depense.reference}"
        success_message = "La depense interne a ete mise a jour."
        context_extra = {"depense": depense}

    if request.method == "POST":
        post_data = request.POST.copy()
        if depense.source_depense == Depense.SOURCE_CHARGEMENT:
            post_data["titre"] = depense.titre
        form = form_class(post_data, instance=depense)
        ligne_values = _build_expression_ligne_values(depense, request=request) if depense.source_depense != Depense.SOURCE_CHARGEMENT else None
        if form.is_valid():
            if depense.source_depense != Depense.SOURCE_CHARGEMENT:
                lignes, ligne_errors = _validate_expression_lignes(request)
                if ligne_errors:
                    for error in ligne_errors:
                        form.add_error(None, error)
                else:
                    depense = form.save()
                    _save_depense_lignes(depense, lignes)
                    journaliser_action(
                        request.user,
                        "Depenses",
                        "Modification expression de besoin",
                        depense.reference,
                        f"{request.user.username} a modifie la depense {depense.reference}.",
                    )
                    messages.success(request, success_message)
                    return redirect("liste_depenses")
            else:
                depense = form.save()
            if depense.source_depense == Depense.SOURCE_CHARGEMENT and depense.type_depense_id:
                depense.libelle_depense = depense.type_depense.libelle
                depense.save(update_fields=["type_depense", "description", "montant_estime", "libelle_depense", "date_mise_a_jour"])
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Modification depense camion",
                    depense.reference,
                    f"{request.user.username} a modifie la depense {depense.reference}.",
                )
                messages.success(request, success_message)
                return redirect("liste_depenses")
    else:
        form = form_class(instance=depense)
        ligne_values = _build_expression_ligne_values(depense) if depense.source_depense != Depense.SOURCE_CHARGEMENT else None

    return render(
        request,
        template_name,
        {
            "form": form,
            "page_title": page_title,
            "is_edit": True,
            "depense": depense,
            "ligne_values": ligne_values,
            **context_extra,
        },
    )


def ajouter_depense_chargement(request, operation_id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "camion", "chauffeur", "commande"),
        id=operation_id,
        remplace_par__isnull=True,
    )
    scope = _get_chargement_scope(request, operation)
    titre_auto = _charging_title_for_scope(operation, scope)
    scope_operations = list(_get_scope_operations(operation, scope))
    commande_multi_bl = _commande_chargee_sur_plusieurs_bl(operation)
    if operation.etat_bon not in {"charge", "livre"} and not operation.date_bons_charges:
        messages.error(request, "Vous ne pouvez saisir une depense camion qu'apres le chargement du BL.")
        return redirect("modifier_operation_logisticien", id=operation.id)

    depense_maitre = _depense_chargement_maitre(operation, scope=scope)
    if depense_maitre and depense_maitre.statut != Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA:
        messages.error(request, "Vous ne pouvez plus ajouter de depense camion apres la decision du DGA.")
        return redirect("modifier_operation_logisticien", id=operation.id)

    if request.method == "POST":
        post_data = request.POST.copy()
        post_data["titre"] = titre_auto
        submit_to_dga = bool(request.POST.get("submit_to_dga"))
        if (
            submit_to_dga
            and depense_maitre
            and depense_maitre.lignes.exists()
            and not (post_data.get("type_depense") or "").strip()
            and not (post_data.get("type_depense_search") or "").strip()
        ):
            messages.success(
                request,
                "Le bon de validation des depenses camion a ete transmis au DGA sans ajouter de nouvelle ligne.",
            )
            return redirect("modifier_operation_logisticien", id=operation.id)
        form = DepenseChargementForm(post_data)
        if form.is_valid():
            form_depense = form.save(commit=False)
            type_depense = form.cleaned_data.get("type_depense")
            if scope == Depense.PORTEE_COMMANDE and type_depense and type_depense.is_carburant_type:
                form.add_error("type_depense_search", "Le carburant doit rester rattache a un BL unique. Utilisez le mode BL pour cette ligne.")
            else:
                if depense_maitre is None:
                    depense = form_depense
                    depense.demandeur = request.user
                    depense.operation = operation
                    depense.commande = operation.commande
                    depense.source_depense = Depense.SOURCE_CHARGEMENT
                    depense.portee_chargement = scope
                    depense.titre = titre_auto
                    depense.libelle_depense = "Lot de depenses camion"
                    depense.statut = Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA
                    depense.mode_reglement = Depense.MODE_ESPECE
                    depense.save()
                    depense_maitre = depense
                else:
                    depense = depense_maitre

                is_fuel = bool(type_depense and type_depense.is_carburant_type)
                quantite_ligne = form.cleaned_data.get("quantite_a_consommer") if is_fuel else Decimal("1")
                prix_unitaire = Decimal("12000") if is_fuel else (form.cleaned_data.get("montant_estime") or Decimal("0"))
                DepenseLigne.objects.create(
                    depense=depense,
                    type_depense=type_depense,
                    designation=type_depense.libelle if type_depense else "Depense camion",
                    commentaire=form.cleaned_data.get("description") or "",
                    date_bon_conso=form.cleaned_data.get("date_bon_conso") if is_fuel else None,
                    quantite=quantite_ligne or Decimal("1"),
                    prix_unitaire=prix_unitaire,
                )

                depense.type_depense = depense.type_depense if depense.type_depense_id and not is_fuel else type_depense
                depense.libelle_depense = "Lot de depenses camion"
                depense.description = _charging_description_for_scope(operation, scope)
                if is_fuel:
                    depense.date_bon_conso = form.cleaned_data.get("date_bon_conso")
                    depense.quantite_a_consommer = form.cleaned_data.get("quantite_a_consommer")
                depense.save()
                _sync_depense_chargement_totals(depense)
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Ajout ligne depense liee au chargement",
                    depense.reference,
                    f"{request.user.username} a ajoute une ligne de depense camion sur {depense.reference} ({_scope_label(scope).lower()}) depuis le BL {operation.numero_bl}.",
                )
                if submit_to_dga:
                    messages.success(
                        request,
                        "La ligne de depense a ete ajoutee puis le bon de validation a ete transmis au DGA.",
                    )
                else:
                    messages.success(
                        request,
                        "La ligne de depense a ete ajoutee au bon de validation "
                        + ("de la commande." if scope == Depense.PORTEE_COMMANDE else "du BL.")
                    )
                if request.POST.get("save_and_add_other"):
                    return redirect(_build_chargement_return_url(operation.id, scope))
                return redirect("modifier_operation_logisticien", id=operation.id)
    else:
        form = DepenseChargementForm(
            initial={
                "titre": titre_auto,
                "description": _charging_description_for_scope(operation, scope),
            }
        )

    depenses_existantes = []
    if depense_maitre:
        for ligne in depense_maitre.lignes.all():
            ligne.montant_affiche = _format_amount(ligne.montant)
            ligne.prix_unitaire_affiche = _format_amount(ligne.prix_unitaire)
            ligne.quantite_affiche = _format_amount(ligne.quantite)
            ligne.is_fuel_related = bool(ligne.type_depense_id and ligne.type_depense.is_carburant_type)
            depenses_existantes.append(ligne)

    return render(
        request,
        "depenses/form_chargement.html",
        {
            "form": form,
            "operation": operation,
            "type_depense_form": TypeDepenseForm(portefeuille=TypeDepense.PORTEFEUILLE_LOGISTIQUE),
            "page_title": f"Nouvelle depense camion - {operation.numero_bl}",
            "depenses_existantes": depenses_existantes,
            "depense_maitre": depense_maitre,
            "can_print_bon_conso": bool(depense_maitre and _depense_has_carburant_line(depense_maitre)),
            "bon_conso_reference_preview": f"BC-{operation.numero_bl}",
            "bon_conso_date_preview": timezone.localdate(),
            "portee_chargement": scope,
            "commande_multi_bl": commande_multi_bl,
            "scope_operations": scope_operations,
            "scope_label": _scope_label(scope),
        },
    )


def modifier_ligne_depense_chargement(request, operation_id, ligne_id):
    operation = get_object_or_404(
        Operation.objects.select_related("client", "camion", "chauffeur", "commande"),
        id=operation_id,
        remplace_par__isnull=True,
    )
    ligne = get_object_or_404(
        DepenseLigne.objects.select_related("depense", "type_depense").filter(
            depense_id__in=_charge_expenses_queryset_for_operation(operation).values("id")
        ),
        id=ligne_id,
    )
    depense_maitre = ligne.depense
    if not _chargement_editable_by_logistique(depense_maitre, request.user) and not is_admin_user(request.user):
        messages.error(request, "Cette ligne ne peut plus etre modifiee.")
        return redirect(_build_chargement_return_url(operation.id, depense_maitre.portee_chargement))

    initial = {
        "titre": depense_maitre.titre,
        "type_depense": ligne.type_depense_id,
        "type_depense_search": ligne.type_depense.libelle if ligne.type_depense_id else ligne.designation,
        "description": ligne.commentaire,
        "montant_estime": ligne.montant if ligne.type_depense_id and ligne.type_depense.is_carburant_type else ligne.prix_unitaire,
        "date_bon_conso": ligne.date_bon_conso,
        "quantite_a_consommer": ligne.quantite if ligne.type_depense_id and ligne.type_depense.is_carburant_type else None,
    }

    if request.method == "POST":
        post_data = request.POST.copy()
        post_data["titre"] = depense_maitre.titre
        form = DepenseChargementForm(post_data, initial=initial)
        if form.is_valid():
            type_depense = form.cleaned_data.get("type_depense")
            if depense_maitre.portee_chargement == Depense.PORTEE_COMMANDE and type_depense and type_depense.is_carburant_type:
                form.add_error("type_depense_search", "Le carburant doit rester rattache a un BL unique. Repassez cette ligne en mode BL.")
            else:
                is_fuel = bool(type_depense and type_depense.is_carburant_type)
                ligne.type_depense = type_depense
                ligne.designation = type_depense.libelle if type_depense else "Depense camion"
                ligne.commentaire = form.cleaned_data.get("description") or ""
                ligne.date_bon_conso = form.cleaned_data.get("date_bon_conso") if is_fuel else None
                ligne.quantite = form.cleaned_data.get("quantite_a_consommer") if is_fuel else Decimal("1")
                ligne.prix_unitaire = Decimal("12000") if is_fuel else (form.cleaned_data.get("montant_estime") or Decimal("0"))
                ligne.save()
                if is_fuel:
                    depense_maitre.date_bon_conso = ligne.date_bon_conso
                    depense_maitre.quantite_a_consommer = ligne.quantite
                    depense_maitre.type_depense = type_depense
                    depense_maitre.save(update_fields=["date_bon_conso", "quantite_a_consommer", "type_depense", "date_mise_a_jour"])
                _sync_depense_chargement_totals(depense_maitre)
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Modification ligne depense chargement",
                    depense_maitre.reference,
                    f"{request.user.username} a modifie une ligne du bon de depense {depense_maitre.reference} pour le BL {operation.numero_bl}.",
                )
                messages.success(request, "La ligne de depense a ete mise a jour.")
                return redirect(_build_chargement_return_url(operation.id, depense_maitre.portee_chargement))
    else:
        form = DepenseChargementForm(initial=initial)

    depenses_existantes = []
    for item in depense_maitre.lignes.all():
        item.montant_affiche = _format_amount(item.montant)
        item.prix_unitaire_affiche = _format_amount(item.prix_unitaire)
        item.quantite_affiche = _format_amount(item.quantite)
        item.is_fuel_related = bool(item.type_depense_id and item.type_depense.is_carburant_type)
        depenses_existantes.append(item)

    return render(
        request,
        "depenses/form_chargement.html",
        {
            "form": form,
            "operation": operation,
            "type_depense_form": TypeDepenseForm(portefeuille=TypeDepense.PORTEFEUILLE_LOGISTIQUE),
            "page_title": f"Modifier une ligne de depense camion - {operation.numero_bl}",
            "depenses_existantes": depenses_existantes,
            "depense_maitre": depense_maitre,
            "can_print_bon_conso": bool(depense_maitre and _depense_has_carburant_line(depense_maitre)),
            "bon_conso_reference_preview": f"BC-{operation.numero_bl}",
            "bon_conso_date_preview": timezone.localdate(),
            "editing_line_id": ligne.id,
            "portee_chargement": depense_maitre.portee_chargement,
            "commande_multi_bl": _commande_chargee_sur_plusieurs_bl(operation),
            "scope_operations": list(_get_scope_operations(operation, depense_maitre.portee_chargement)),
            "scope_label": _scope_label(depense_maitre.portee_chargement),
        },
    )


def engagement_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    if depense.statut != Depense.STATUT_ATTENTE_ENGAGEMENT and not is_admin_user(request.user):
        messages.error(request, "Cette fiche n'est pas disponible pour l'engagement achat.")
        return redirect("liste_depenses")

    if request.method == "POST":
        form = DepenseEngagementForm(request.POST, request.FILES, instance=depense, entite_reference=depense.entite_depense)
        ligne_values = _build_ligne_values(depense, request=request)
        if form.is_valid():
            lignes, ligne_errors = _validate_lignes(request)
            if ligne_errors:
                for error in ligne_errors:
                    form.add_error(None, error)
            else:
                depense = form.save(commit=False)
                depense.engagement_saisi_par = request.user
                depense.engagement_saisi_le = timezone.now()
                depense.statut = Depense.STATUT_ATTENTE_ENGAGEMENT
                depense.save()
                _save_depense_justificatifs(depense, request.FILES.getlist("pieces_justificatives"))
                _save_depense_lignes(depense, lignes)
                depense.statut = Depense.STATUT_ATTENTE_VALIDATION_DGA
                depense.save(update_fields=["statut"])
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Engagement depense",
                    depense.reference,
                    f"{request.user.username} a saisi l'engagement detaille de la depense {depense.reference}.",
                )
                messages.success(request, f"L'engagement a ete enregistre et transmis au {_internal_dga_label(depense)}.")
                return redirect("liste_depenses")
    else:
        form = DepenseEngagementForm(instance=depense, entite_reference=depense.entite_depense)
        ligne_values = _build_ligne_values(depense)

    return render(
        request,
        "depenses/form_engagement.html",
        {
            "form": form,
            "depense": depense,
            "internal_dga_label": _internal_dga_label(depense),
            "type_depense_form": TypeDepenseForm(portefeuille=TypeDepense.PORTEFEUILLE_INTERNE, entite_reference=depense.entite_depense),
            "lieu_projet_form": LieuProjetForm(entite_reference=depense.entite_depense),
            "ligne_values": ligne_values,
        },
    )


def valider_expression_depense(request, id):
    depense = get_object_or_404(Depense, id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION:
        messages.error(request, "Cette expression n'attend plus de validation.")
        return redirect("liste_depenses")

    role_name = get_user_role(request.user) or "administrateur"
    depense.expression_decision_dga = Depense.DECISION_VALIDEE
    depense.expression_decision_dga_par = request.user
    depense.expression_decision_dga_le = timezone.now()
    depense.expression_decision_dga_motif = ""
    depense.statut = Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG
    depense.save(
        update_fields=[
            "expression_decision_dga",
            "expression_decision_dga_par",
            "expression_decision_dga_le",
            "expression_decision_dga_motif",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Depenses",
        "Validation expression de besoin DGA SOGEFI",
        depense.reference,
        f"{request.user.username} a valide l'expression de besoin {depense.reference}.",
    )
    messages.success(request, "Decision DGA SOGEFI enregistree et expression transmise au DG.")
    return redirect("liste_depenses")


def rejeter_expression_depense(request, id):
    depense = get_object_or_404(Depense, id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION:
        messages.error(request, "Cette expression n'attend plus de decision.")
        return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        depense.expression_decision_dga = Depense.DECISION_REJETEE
        depense.expression_decision_dga_par = request.user
        depense.expression_decision_dga_le = timezone.now()
        depense.expression_decision_dga_motif = motif
        depense.statut = Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG
        depense.save(
            update_fields=[
                "expression_decision_dga",
                "expression_decision_dga_par",
                "expression_decision_dga_le",
                "expression_decision_dga_motif",
                "statut",
            ]
        )
        journaliser_action(
            request.user,
            "Depenses",
            "Rejet expression de besoin DGA SOGEFI",
            depense.reference,
            f"{request.user.username} a rejete l'expression de besoin {depense.reference}.",
        )
        messages.success(request, "Decision DGA SOGEFI enregistree et expression transmise au DG.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def valider_expression_depense_dg(request, id):
    depense = get_object_or_404(Depense, id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG or not depense.expression_decidee_par_dga():
        messages.error(request, "Le DG ne peut intervenir qu'apres la decision du DGA SOGEFI.")
        return redirect("liste_depenses")

    role_name = get_user_role(request.user) or "administrateur"
    depense.expression_decision_dg = Depense.DECISION_VALIDEE
    depense.expression_decision_dg_par = request.user
    depense.expression_decision_dg_le = timezone.now()
    depense.expression_decision_dg_motif = ""
    depense.expression_validee_par = request.user
    depense.expression_validee_le = timezone.now()
    depense.expression_validee_role = role_name
    depense.motif_rejet_expression = ""
    depense.statut = Depense.STATUT_ATTENTE_ENGAGEMENT
    depense.save(
        update_fields=[
            "expression_decision_dg",
            "expression_decision_dg_par",
            "expression_decision_dg_le",
            "expression_decision_dg_motif",
            "expression_validee_par",
            "expression_validee_le",
            "expression_validee_role",
            "motif_rejet_expression",
            "statut",
        ]
    )
    journaliser_action(
        request.user,
        "Depenses",
        "Validation expression de besoin DG",
        depense.reference,
        f"{request.user.username} a valide l'expression de besoin {depense.reference} apres la decision DGA SOGEFI.",
    )
    messages.success(request, "Le DG a valide l'expression de besoin.")
    return redirect("liste_depenses")


def rejeter_expression_depense_dg(request, id):
    depense = get_object_or_404(Depense, id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG or not depense.expression_decidee_par_dga():
        messages.error(request, "Le DG ne peut intervenir qu'apres la decision du DGA SOGEFI.")
        return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        depense.expression_decision_dg = Depense.DECISION_REJETEE
        depense.expression_decision_dg_par = request.user
        depense.expression_decision_dg_le = timezone.now()
        depense.expression_decision_dg_motif = motif
        depense.motif_rejet_expression = motif
        depense.statut = Depense.STATUT_REJETEE_EXPRESSION
        depense.save(
            update_fields=[
                "expression_decision_dg",
                "expression_decision_dg_par",
                "expression_decision_dg_le",
                "expression_decision_dg_motif",
                "motif_rejet_expression",
                "statut",
            ]
        )
        journaliser_action(
            request.user,
            "Depenses",
            "Rejet expression de besoin DG",
            depense.reference,
            f"{request.user.username} a rejete l'expression de besoin {depense.reference} apres la decision DGA SOGEFI.",
        )
        messages.success(request, "Le DG a rejete l'expression de besoin.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def valider_depense_chargement_dga(request, id):
    depense = get_object_or_404(Depense, id=id, source_depense=Depense.SOURCE_CHARGEMENT)
    if request.method != "POST":
        return redirect("liste_depenses")
    if not _chargement_dga_pending(depense):
        messages.error(request, "Cette depense camion n'attend plus de validation DGA.")
        return redirect("liste_depenses")

    timestamp = timezone.now()
    for depense_lot in _depenses_chargement_lot(depense).filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA):
        depense_lot.expression_decision_dga = Depense.DECISION_VALIDEE
        depense_lot.expression_decision_dga_par = request.user
        depense_lot.expression_decision_dga_le = timestamp
        depense_lot.expression_decision_dga_motif = ""
        depense_lot.statut = Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
        depense_lot.save(
            update_fields=[
                "expression_decision_dga",
                "expression_decision_dga_par",
                "expression_decision_dga_le",
                "expression_decision_dga_motif",
                "statut",
            ]
        )
    messages.success(request, "Le lot de depenses camion du BL a ete valide par le DGA.")
    return redirect("liste_depenses")


def rejeter_depense_chargement_dga(request, id):
    depense = get_object_or_404(Depense, id=id, source_depense=Depense.SOURCE_CHARGEMENT)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if not _chargement_dga_pending(depense):
        messages.error(request, "Cette depense camion n'attend plus de decision DGA.")
        return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        timestamp = timezone.now()
        for depense_lot in _depenses_chargement_lot(depense).filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA):
            depense_lot.expression_decision_dga = Depense.DECISION_REJETEE
            depense_lot.expression_decision_dga_par = request.user
            depense_lot.expression_decision_dga_le = timestamp
            depense_lot.expression_decision_dga_motif = motif
            depense_lot.statut = Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG
            depense_lot.save(
                update_fields=[
                    "expression_decision_dga",
                    "expression_decision_dga_par",
                    "expression_decision_dga_le",
                    "expression_decision_dga_motif",
                    "statut",
                ]
            )
        messages.success(request, "La decision DGA a ete enregistree pour tout le lot de depenses du BL et transmise au DG.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def valider_depense_chargement_dg(request, id):
    depense = get_object_or_404(Depense, id=id, source_depense=Depense.SOURCE_CHARGEMENT)
    if not _chargement_dg_pending(depense):
        messages.error(request, "Le DG ne peut intervenir qu'apres la decision du DGA.")
        return redirect("liste_depenses")
    if request.method == "POST":
        form = DepenseDecisionEngagementForm(request.POST)
        if form.is_valid():
            mode = form.cleaned_data.get("mode_reglement")
            if mode not in {Depense.MODE_CHEQUE, Depense.MODE_ESPECE}:
                form.add_error("mode_reglement", "Le DG doit choisir cheque ou espece.")
            else:
                timestamp = timezone.now()
                target_status = (
                    Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE
                    if mode == Depense.MODE_CHEQUE
                    else Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE
                )
                for depense_lot in _depenses_chargement_lot(depense).filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG):
                    depense_lot_montant_comptable = depense_lot.montant_comptable or Decimal("0")
                    depense_lot.expression_decision_dg = Depense.DECISION_VALIDEE
                    depense_lot.expression_decision_dg_par = request.user
                    depense_lot.expression_decision_dg_le = timestamp
                    depense_lot.expression_decision_dg_motif = ""
                    depense_lot.mode_reglement = mode
                    if depense_lot_montant_comptable > 0:
                        depense_lot.statut = target_status
                        update_fields = [
                            "expression_decision_dg",
                            "expression_decision_dg_par",
                            "expression_decision_dg_le",
                            "expression_decision_dg_motif",
                            "mode_reglement",
                            "statut",
                        ]
                    else:
                        depense_lot.statut = Depense.STATUT_PAYEE
                        depense_lot.date_paiement = timezone.localdate()
                        depense_lot.paiement_observation = "Carburant non comptabilise : bon de consommation uniquement."
                        update_fields = [
                            "expression_decision_dg",
                            "expression_decision_dg_par",
                            "expression_decision_dg_le",
                            "expression_decision_dg_motif",
                            "mode_reglement",
                            "statut",
                            "date_paiement",
                            "paiement_observation",
                        ]
                    depense_lot.save(
                        update_fields=update_fields
                    )
                messages.success(request, "Le lot de depenses camion a ete valide par le DG. Le carburant reste informatif et n'est pas transmis au paiement.")
                return redirect("liste_depenses")
    else:
        form = DepenseDecisionEngagementForm(initial={"mode_reglement": depense.mode_reglement})

    return render(
        request,
        "depenses/decision_dg.html",
        {
            "form": form,
            "depense": depense,
            "decision_scope_label": "depense camion / BL",
        },
    )


def rejeter_depense_chargement_dg(request, id):
    depense = get_object_or_404(Depense, id=id, source_depense=Depense.SOURCE_CHARGEMENT)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if not _chargement_dg_pending(depense):
        messages.error(request, "Le DG ne peut intervenir qu'apres la decision du DGA.")
        return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        timestamp = timezone.now()
        for depense_lot in _depenses_chargement_lot(depense).filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG):
            depense_lot.expression_decision_dg = Depense.DECISION_REJETEE
            depense_lot.expression_decision_dg_par = request.user
            depense_lot.expression_decision_dg_le = timestamp
            depense_lot.expression_decision_dg_motif = motif
            depense_lot.statut = Depense.STATUT_REJETEE_CHARGEMENT
            depense_lot.save(
                update_fields=[
                    "expression_decision_dg",
                    "expression_decision_dg_par",
                    "expression_decision_dg_le",
                    "expression_decision_dg_motif",
                    "statut",
                ]
            )
        messages.success(request, "Le lot de depenses camion a ete rejete par le DG.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def valider_engagement_dga(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_DGA:
        messages.error(request, "Cette depense n'est pas en attente de validation DGA.")
        return redirect("liste_depenses")
    role = get_user_role(request.user)
    if not is_admin_user(request.user):
        if role != _internal_dga_role(depense):
            messages.error(request, f"Seul le {_internal_dga_label(depense)} peut valider cette depense.")
            return redirect("liste_depenses")
    depense.validation_dga_par = request.user
    depense.validation_dga_le = timezone.now()
    depense.engagement_decision_dga = Depense.DECISION_VALIDEE
    depense.motif_rejet_dga = ""
    depense.statut = Depense.STATUT_ATTENTE_VALIDATION_DG
    depense.save(update_fields=["validation_dga_par", "validation_dga_le", "engagement_decision_dga", "motif_rejet_dga", "statut"])
    journaliser_action(
        request.user,
        "Depenses",
        f"Validation engagement {_internal_dga_label(depense)}",
        depense.reference,
        f"{request.user.username} a valide l'engagement de la depense {depense.reference}.",
    )
    messages.success(request, f"L'engagement a ete valide par le {_internal_dga_label(depense)}.")
    return redirect("liste_depenses")


def rejeter_engagement_dga(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_DGA:
        messages.error(request, "Cette depense n'est pas en attente de validation DGA.")
        return redirect("liste_depenses")
    role = get_user_role(request.user)
    if not is_admin_user(request.user):
        if role != _internal_dga_role(depense):
            messages.error(request, f"Seul le {_internal_dga_label(depense)} peut rejeter cette depense.")
            return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        depense.validation_dga_par = request.user
        depense.validation_dga_le = timezone.now()
        depense.engagement_decision_dga = Depense.DECISION_REJETEE
        depense.motif_rejet_dga = motif
        depense.statut = Depense.STATUT_ATTENTE_VALIDATION_DG
        depense.save(
            update_fields=[
                "validation_dga_par",
                "validation_dga_le",
                "engagement_decision_dga",
                "motif_rejet_dga",
                "statut",
            ]
        )
        journaliser_action(
            request.user,
            "Depenses",
            f"Rejet engagement {_internal_dga_label(depense)}",
            depense.reference,
            f"{request.user.username} a rejete l'engagement de la depense {depense.reference}.",
        )
        messages.success(request, f"Decision {_internal_dga_label(depense)} enregistree et engagement transmis au DG.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def valider_engagement_dg(request, id):
    depense = get_object_or_404(Depense, id=id)
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_DG or not depense.engagement_decide_par_dga():
        messages.error(request, "Cette depense n'est pas en attente de validation DG.")
        return redirect("liste_depenses")
    if request.method == "POST":
        form = DepenseDecisionEngagementForm(request.POST)
        if form.is_valid():
            mode = form.cleaned_data.get("mode_reglement")
            if mode not in {Depense.MODE_CHEQUE, Depense.MODE_ESPECE}:
                form.add_error("mode_reglement", "Le DG doit choisir cheque ou espece.")
            else:
                depense.validation_dg_par = request.user
                depense.validation_dg_le = timezone.now()
                depense.engagement_decision_dg = Depense.DECISION_VALIDEE
                depense.mode_reglement = mode
                depense.motif_rejet_dg = ""
                depense.statut = (
                    Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE
                    if mode == Depense.MODE_CHEQUE
                    else Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE
                )
                depense.save(
                    update_fields=[
                        "validation_dg_par",
                        "validation_dg_le",
                        "engagement_decision_dg",
                        "mode_reglement",
                        "motif_rejet_dg",
                        "statut",
                    ]
                )
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Validation engagement DG",
                    depense.reference,
                    f"{request.user.username} a valide l'engagement de la depense {depense.reference} en mode {mode}.",
                )
                messages.success(request, "La depense a ete validee par le DG et transmise au paiement.")
                return redirect("liste_depenses")
    else:
        form = DepenseDecisionEngagementForm(initial={"mode_reglement": depense.mode_reglement})

    return render(
        request,
        "depenses/decision_dg.html",
        {
            "form": form,
            "depense": depense,
        },
    )


def rejeter_engagement_dg(request, id):
    depense = get_object_or_404(Depense, id=id)
    if request.method != "POST":
        return redirect("liste_depenses")
    form = DepenseDecisionExpressionForm(request.POST)
    if depense.statut != Depense.STATUT_ATTENTE_VALIDATION_DG or not depense.engagement_decide_par_dga():
        messages.error(request, "Cette depense n'est pas en attente de validation DG.")
        return redirect("liste_depenses")
    if form.is_valid():
        motif = (form.cleaned_data.get("motif_rejet") or "").strip()
        if not motif:
            messages.error(request, "Le motif de rejet est obligatoire.")
            return redirect("liste_depenses")
        depense.validation_dg_par = request.user
        depense.validation_dg_le = timezone.now()
        depense.engagement_decision_dg = Depense.DECISION_REJETEE
        depense.statut = Depense.STATUT_REJETEE_DG
        depense.motif_rejet_dg = motif
        depense.save(
            update_fields=[
                "validation_dg_par",
                "validation_dg_le",
                "engagement_decision_dg",
                "statut",
                "motif_rejet_dg",
            ]
        )
        journaliser_action(
            request.user,
            "Depenses",
            "Rejet engagement DG",
            depense.reference,
            f"{request.user.username} a rejete l'engagement de la depense {depense.reference}.",
        )
        messages.success(request, "L'engagement a ete rejete par le DG.")
    else:
        messages.error(request, "Le motif de rejet est invalide.")
    return redirect("liste_depenses")


def apercu_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    role = get_user_role(request.user)
    return render(
        request,
        "depenses/apercu_depense.html",
        {
            **_build_preview_context(depense),
            "user_role": role,
            "can_decide_expression_dga": role in {"dga_sogefi", "dga_avena"} and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION,
            "can_decide_expression_dg": role == "directeur" and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG and depense.expression_decidee_par_dga(),
            "can_decide_chargement_dga": (is_admin_user(request.user) or role == "dga") and _chargement_dga_pending(depense),
            "can_decide_chargement_dg": role == "directeur" and _chargement_dg_pending(depense),
            "can_decide_engagement_dga": role == _internal_dga_role(depense) and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_DGA,
            "can_decide_engagement_dg": role == "directeur" and depense.statut == Depense.STATUT_ATTENTE_VALIDATION_DG and depense.engagement_decide_par_dga(),
        },
    )


def imprimer_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    return render(
        request,
        "depenses/imprimer_depense.html",
        _build_preview_context(depense),
    )


def bon_consommation_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id, source_depense=Depense.SOURCE_CHARGEMENT)
    if not _depense_has_carburant_line(depense):
        messages.error(request, "Cette depense ne correspond pas a un bon de consommation carburant.")
        return redirect("apercu_depense", id=depense.id)
    operation = depense.operation
    return render(
        request,
        "depenses/bon_consommation.html",
        {
            "depense": depense,
            "operation": operation,
            "date_bon_conso_affichee": depense.date_bon_conso or timezone.localdate(),
            "bon_conso_reference": depense.bon_consommation_reference,
            "quantite_transportee_affichee": _format_amount(operation.quantite if operation else 0),
            "quantite_a_consommer_affichee": _format_amount(depense.quantite_a_consommer),
            "montant_bon_conso_affiche": _format_amount(depense.montant_estime),
            "ville_depart": operation.commande.ville_depart if operation and operation.commande_id else "-",
            "destination": operation.destination if operation else "-",
            "copies": ["Exemplaire logistique", "Exemplaire chauffeur", "Exemplaire gerant"],
        },
    )


def paiement_depense(request, id):
    depense = get_object_or_404(_depenses_queryset_for_user(request.user), id=id)
    if not _paiement_allowed(request.user, depense):
        messages.error(request, "Vous n'avez pas acces a ce paiement.")
        return redirect("paiements_maintenances")
    if depense.statut not in {Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE, Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE} and not is_admin_user(request.user):
        messages.error(request, "Cette depense n'est plus en attente de paiement.")
        return redirect("paiements_maintenances")
    payment_role = get_user_role(request.user)
    caisse_metrics = None
    solde_courant = None
    montant_a_payer = depense.montant_comptable or Decimal("0")
    if depense.source_depense == Depense.SOURCE_CHARGEMENT and montant_a_payer <= 0:
        messages.info(request, "Cette ligne carburant est un bon de consommation informatif et ne passe pas en paiement caisse/comptable.")
        return redirect("paiements_maintenances")
    if depense.mode_reglement == Depense.MODE_ESPECE and payment_role in {"caissiere", "caissiere_soni"}:
        from maintenance.views import _get_caisse_metrics
        caisse_metrics = _get_caisse_metrics(request.user)
        solde_courant = caisse_metrics["solde"]

    if request.method == "POST":
        form = DepensePaiementForm(request.POST, instance=depense)
        if form.is_valid():
            if solde_courant is not None and montant_a_payer > solde_courant:
                form.add_error(
                    None,
                    f"Paiement impossible : le montant de { _format_amount(montant_a_payer) } GNF depasse le solde disponible de { _format_amount(solde_courant) } GNF.",
                )
            else:
                depense = form.save(commit=False)
                if not depense.date_paiement:
                    depense.date_paiement = timezone.localdate()
                depense.paiement_saisi_par = request.user
                depense.paiement_saisi_le = timezone.now()
                depense.statut = Depense.STATUT_PAYEE
                depense.save()
                journaliser_action(
                    request.user,
                    "Depenses",
                    "Paiement depense",
                    depense.reference,
                    f"{request.user.username} a enregistre le paiement de la depense {depense.reference}.",
                )
                messages.success(request, "Le paiement a ete enregistre.")
                return redirect("paiements_maintenances")
    else:
        form = DepensePaiementForm(instance=depense)

    return render(
        request,
        "depenses/form_paiement.html",
        {
            "form": form,
            "depense": depense,
            "payment_role": get_user_role(request.user),
            "is_cheque_payment": depense.mode_reglement == Depense.MODE_CHEQUE,
            "is_espece_payment": depense.mode_reglement == Depense.MODE_ESPECE,
            "banques": Banque.objects.filter(actif=True).order_by("nom"),
            "banque_form": BanqueForm(),
            "type_piece_form": TypePieceIdentiteForm(),
            "type_pieces": TypePieceIdentite.objects.order_by("libelle"),
            "solde_courant_display": _format_amount(solde_courant) if solde_courant is not None else None,
            "montant_a_payer_display": _format_amount(montant_a_payer),
            "solde_apres_display": _format_amount((solde_courant - montant_a_payer) if solde_courant is not None else Decimal("0")) if solde_courant is not None else None,
            "caisse_insuffisante": bool(solde_courant is not None and montant_a_payer > solde_courant),
        },
    )


def ajouter_type_depense_modal(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "errors": {"__all__": ["Requete invalide."]}}, status=405)
    portefeuille = (request.POST.get("portefeuille") or "").strip() or _type_depense_portefeuille_for_role(request.user)
    entite_reference = _reference_entity_for_user(request.user)
    form = TypeDepenseForm(request.POST, portefeuille=portefeuille, entite_reference=entite_reference)
    if form.is_valid():
        type_depense = form.save()
        return JsonResponse(
            {
                "success": True,
                "type_depense": {
                    "id": type_depense.id,
                    "label": type_depense.libelle,
                    "montant_defaut": str(type_depense.montant_defaut),
                    "is_carburant": type_depense.is_carburant_type,
                },
            }
        )
    errors = {
        field: [item["message"] for item in messages_list]
        for field, messages_list in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_lieu_projet_modal(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "errors": {"__all__": ["Requete invalide."]}}, status=405)
    form = LieuProjetForm(request.POST, entite_reference=_reference_entity_for_user(request.user))
    if form.is_valid():
        lieu = form.save()
        return JsonResponse(
            {
                "success": True,
                "lieu_projet": {"id": lieu.id, "label": lieu.libelle},
            }
        )
    errors = {
        field: [item["message"] for item in messages_list]
        for field, messages_list in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


def ajouter_type_piece_identite_modal(request):
    if request.method != "POST":
        return JsonResponse({"success": False, "errors": {"__all__": ["Requete invalide."]}}, status=405)
    form = TypePieceIdentiteForm(request.POST)
    if form.is_valid():
        type_piece = form.save()
        return JsonResponse(
            {
                "success": True,
                "type_piece": {
                    "id": type_piece.id,
                    "label": type_piece.libelle,
                },
            }
        )
    errors = {
        field: [item["message"] for item in messages_list]
        for field, messages_list in form.errors.get_json_data().items()
    }
    return JsonResponse({"success": False, "errors": errors}, status=400)


liste_depenses = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "comptable_sogefi",
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "controleur",
    "dga",
    "dga_sogefi",
    "dga_avena",
    "directeur",
    "invite",
    "logistique",
    "maintenancier",
    "responsable_achat",
    "transitaire",
)(liste_depenses)
liste_types_depense = role_required("dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena", "directeur")(liste_types_depense)
modifier_type_depense = role_required("dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena", "directeur")(modifier_type_depense)
supprimer_type_depense = role_required("dga_sogefi", "responsable_achat", "dga_avena", "comptable_avena", "directeur")(supprimer_type_depense)
ajouter_depense = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "comptable_sogefi",
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "controleur",
    "dga",
    "dga_sogefi",
    "dga_avena",
    "directeur",
    "invite",
    "logistique",
    "maintenancier",
    "responsable_achat",
    "transitaire",
)(ajouter_depense)
ajouter_depense_chargement = role_required("logistique")(ajouter_depense_chargement)
modifier_depense = role_required(
    "commercial",
    "responsable_commercial",
    "comptable",
    "comptable_sogefi",
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "controleur",
    "dga",
    "dga_sogefi",
    "dga_avena",
    "directeur",
    "invite",
    "logistique",
    "maintenancier",
    "responsable_achat",
    "transitaire",
)(modifier_depense)
modifier_ligne_depense_chargement = role_required("logistique")(modifier_ligne_depense_chargement)
engagement_depense = role_required("responsable_achat", "logistique", "comptable_avena", "directeur")(engagement_depense)
valider_expression_depense = role_required("dga_sogefi", "dga_avena", "directeur")(valider_expression_depense)
rejeter_expression_depense = role_required("dga_sogefi", "dga_avena", "directeur")(rejeter_expression_depense)
valider_expression_depense_dg = role_required("directeur")(valider_expression_depense_dg)
rejeter_expression_depense_dg = role_required("directeur")(rejeter_expression_depense_dg)
valider_depense_chargement_dga = role_required("dga")(valider_depense_chargement_dga)
rejeter_depense_chargement_dga = role_required("dga")(rejeter_depense_chargement_dga)
valider_depense_chargement_dg = role_required("directeur")(valider_depense_chargement_dg)
rejeter_depense_chargement_dg = role_required("directeur")(rejeter_depense_chargement_dg)
valider_engagement_dga = role_required("dga", "dga_sogefi", "dga_avena")(valider_engagement_dga)
rejeter_engagement_dga = role_required("dga", "dga_sogefi", "dga_avena")(rejeter_engagement_dga)
valider_engagement_dg = role_required("directeur")(valider_engagement_dg)
rejeter_engagement_dg = role_required("directeur")(rejeter_engagement_dg)
paiement_depense = role_required("comptable", "comptable_sogefi", "comptable_avena", "caissiere", "caissiere_soni", "caissiere_avena")(paiement_depense)
ajouter_type_depense_modal = role_required("responsable_achat", "comptable_avena", "directeur", "logistique", "dga_sogefi", "dga_avena")(ajouter_type_depense_modal)
ajouter_lieu_projet_modal = role_required("responsable_achat", "comptable_avena", "directeur", "dga_sogefi", "dga_avena", "logistique")(ajouter_lieu_projet_modal)
apercu_depense = role_required(
    "dga",
    "dga_sogefi",
    "dga_avena",
    "directeur",
    "responsable_achat",
    "comptable_sogefi",
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "commercial",
    "responsable_commercial",
    "comptable",
    "controleur",
    "logistique",
    "maintenancier",
    "transitaire",
    "invite",
)(apercu_depense)
imprimer_depense = role_required(
    "dga",
    "dga_sogefi",
    "dga_avena",
    "directeur",
    "responsable_achat",
    "comptable_sogefi",
    "comptable_avena",
    "caissiere",
    "caissiere_soni",
    "caissiere_avena",
    "commercial",
    "responsable_commercial",
    "comptable",
    "controleur",
    "logistique",
    "maintenancier",
    "transitaire",
    "invite",
)(imprimer_depense)
bon_consommation_depense = role_required(
    "dga",
    "dga_sogefi",
    "directeur",
    "responsable_achat",
    "comptable_sogefi",
    "caissiere",
    "caissiere_soni",
    "commercial",
    "responsable_commercial",
    "comptable",
    "controleur",
    "logistique",
    "maintenancier",
    "transitaire",
    "invite",
)(bon_consommation_depense)
rapport_depenses_internes = role_required("responsable_achat", "logistique", "dga", "dga_sogefi", "dga_avena", "comptable", "comptable_sogefi", "comptable_avena", "directeur")(rapport_depenses_internes)
export_rapport_depenses_internes_xls = role_required("responsable_achat", "logistique", "dga", "dga_sogefi", "dga_avena", "comptable", "comptable_sogefi", "comptable_avena", "directeur")(export_rapport_depenses_internes_xls)
export_rapport_depenses_internes_pdf = role_required("responsable_achat", "logistique", "dga", "dga_sogefi", "dga_avena", "comptable", "comptable_sogefi", "comptable_avena", "directeur")(export_rapport_depenses_internes_pdf)
