from django.db.models import Q

from .models import MessageInterne, ProfilUtilisateur
from .permissions import (
    build_user_permissions,
    can_use_global_entity_selector,
    get_active_supervision_entity,
    get_active_supervision_entity_label,
    get_allowed_supervision_entities,
    get_user_role,
    is_admin_user,
)


def _add_notification(items, label, count, url, tone="default", detail=""):
    if count:
        items.append(
            {
                "label": label,
                "detail": detail,
                "count": count,
                "url": url,
                "tone": tone,
            }
        )


def _cash_depenses_queryset_for_role(Depense, role):
    queryset = Depense.objects.filter(
        statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
        mode_reglement=Depense.MODE_ESPECE,
    )
    if role == "caissiere_soni":
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
    if role == "caissiere_avena":
        return queryset.filter(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_AVENA)
    if role == "caissiere":
        return queryset.filter(
            Q(source_depense=Depense.SOURCE_CHARGEMENT)
            | Q(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SOGEFI)
        )
    return queryset


def _depenses_dg_validation_breakdown(Depense):
    internal_statuses = [
        Depense.STATUT_ATTENTE_VALIDATION_EXPRESSION_DG,
        Depense.STATUT_ATTENTE_VALIDATION_DG,
    ]
    return [
        {
            "label": "Dep. interne SOGEFI",
            "detail": "Validation DG depenses internes SOGEFI",
            "count": Depense.objects.filter(
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SOGEFI,
                statut__in=internal_statuses,
            ).count(),
            "url": "/depenses/?statut=attente_validation_dg_engagement&entite=sogefi",
            "tone": "danger",
        },
        {
            "label": "Dep. interne SONI",
            "detail": "Validation DG depenses internes SONI",
            "count": Depense.objects.filter(
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SONI,
                statut__in=internal_statuses,
            ).count(),
            "url": "/depenses/?statut=attente_validation_dg_engagement&entite=soni",
            "tone": "danger",
        },
        {
            "label": "Dep. interne Avena",
            "detail": "Validation DG depenses internes Avena",
            "count": Depense.objects.filter(
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_AVENA,
                statut__in=internal_statuses,
            ).count(),
            "url": "/depenses/?statut=attente_validation_dg_engagement&entite=avena",
            "tone": "danger",
        },
        {
            "label": "Dep. maint/charg. SOGEFI",
            "detail": "Depenses BL et chargement issues du circuit logistique",
            "count": Depense.objects.filter(
                source_depense=Depense.SOURCE_CHARGEMENT,
                statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DG,
            ).count(),
            "url": "/depenses/?statut=attente_validation_chargement_dg",
            "tone": "warning",
        },
    ]


def _depenses_chargement_queryset_for_operation(Depense, operation):
    base_queryset = Depense.objects.filter(source_depense=Depense.SOURCE_CHARGEMENT)
    if operation.commande_id:
        return base_queryset.filter(
            Q(operation_id=operation.id, portee_chargement=Depense.PORTEE_BL)
            | Q(commande_id=operation.commande_id, portee_chargement=Depense.PORTEE_COMMANDE)
        ).distinct()
    return base_queryset.filter(operation_id=operation.id)


def _depense_chargement_stage(Depense, operation):
    depenses_chargement = list(_depenses_chargement_queryset_for_operation(Depense, operation))
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


def _count_charged_bl_with_pending_depenses(Depense, operations):
    return sum(
        1
        for operation in operations
        if _depense_chargement_stage(Depense, operation) in {"logistique", "dga", "dg", "paiement"}
    )


def _topbar_notifications(user, active_entity=""):
    if not getattr(user, "is_authenticated", False):
        return []

    try:
        from clients.models import Client
        from commandes.models import Commande
        from depenses.models import Depense
        from maintenance.models import Maintenance
        from operations.models import DemandeNouveauBL, Operation
    except Exception:
        return []

    role = get_user_role(user)
    is_admin = is_admin_user(user)
    items = []
    active_operations = Operation.objects.filter(remplace_par__isnull=True)

    if active_entity in {"sogefi", "avena"} and (is_admin or role in {"directeur", "controleur"}):
        entite = Depense.ENTITE_AVENA if active_entity == "avena" else Depense.ENTITE_SOGEFI
        label = "Avena" if active_entity == "avena" else "SOGEFI"
        _add_notification(
            items,
            "Achat",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=entite,
            ).count(),
            "/depenses/?statut=attente_engagement_achat",
            "info",
        )
        _add_notification(
            items,
            "Validation",
            Depense.objects.filter(
                statut__in=[Depense.STATUT_ATTENTE_VALIDATION_DGA, Depense.STATUT_ATTENTE_VALIDATION_DG],
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=entite,
            ).count(),
            "/depenses/?statut=attente_validation_dga_engagement",
            "warning",
        )
        _add_notification(
            items,
            "Cheques",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=entite,
            ).count(),
            "/maintenance/paiements/",
            "info",
        )
        _add_notification(
            items,
            "Caisse",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=entite,
            ).count(),
            "/maintenance/paiements/",
            "danger",
        )
        if not items:
            _add_notification(items, label, 0, "/depenses/", "info")
        return items[:6]

    if role == "secretaire" or is_admin:
        _add_notification(items, "BL", active_operations.filter(etat_bon="initie").count(), "/operations/secretaire/", "info")

    if role == "transitaire" or is_admin:
        _add_notification(
            items,
            "Transit",
            active_operations.filter(etat_bon="attente_reception_transitaire").count(),
            "/operations/transitaire/?etat=attente_reception_transitaire",
            "warning",
        )

    if role == "logistique" or is_admin:
        _add_notification(
            items,
            "Commandes",
            Commande.objects.filter(statut="validee_dg").count(),
            "/commandes/?statut=validee_dg",
            "danger",
        )
        _add_notification(
            items,
            "Reception",
            active_operations.filter(etat_bon="attente_reception_logistique").count(),
            "/operations/logisticien/?etat=attente_reception_logistique",
            "warning",
        )
        _add_notification(
            items,
            "Remise",
            active_operations.filter(etat_bon="liquide_logistique").count(),
            "/operations/logisticien/?etat=liquide_logistique",
            "warning",
        )
        bl_charges = _count_charged_bl_with_pending_depenses(
            Depense,
            active_operations.filter(etat_bon="charge"),
        )
        _add_notification(items, "Charges", bl_charges, "/operations/logisticien/?etat=charge&depense_niveau=action_requise", "info")
        _add_notification(
            items,
            "Retours",
            active_operations.filter(etat_bon="livre", date_bon_retour__isnull=True).count(),
            "/operations/logisticien/?etat=livre&retour=attendu",
            "danger",
        )
        _add_notification(
            items,
            "Depenses",
            Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA).count(),
            "/depenses/?statut=attente_validation_chargement_dga",
            "info",
        )
        _add_notification(items, "Prix", Maintenance.objects.filter(statut="attente_prix").count(), "/maintenance/achat/", "info")

    if role == "chef_chauffeur" or is_admin:
        _add_notification(
            items,
            "Chauffeur",
            active_operations.filter(etat_bon__in=["liquide_chauffeur", "charge"]).count(),
            "/operations/chef-chauffeur/",
            "warning",
        )

    if role == "comptable_client_soni" or is_admin:
        _add_notification(
            items,
            "Livraisons",
            active_operations.filter(etat_bon="livre", livraison_confirmee_client=False).count(),
            "/operations/comptable-client/",
            "warning",
        )
        _add_notification(
            items,
            "SGP",
            active_operations.filter(etat_bon="livre", livraison_confirmee_client=True, ville_perequation_sgp__isnull=True).count(),
            "/operations/comptable-client/perequation/",
            "info",
        )

    if role == "comptable" or is_admin:
        _add_notification(
            items,
            "Nouveau BL",
            DemandeNouveauBL.objects.filter(statut=DemandeNouveauBL.STATUT_EN_ATTENTE).count(),
            "/operations/comptable/?scope=historique",
            "danger",
        )
        _add_notification(
            items,
            "Factures",
            active_operations.filter(etat_bon="livre").filter(Q(numero_facture="") | Q(numero_facture__isnull=True)).count(),
            "/operations/facturation/?statut_facture=a_facturer",
            "warning",
        )
        _add_notification(
            items,
            "Cheques",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
                mode_reglement=Depense.MODE_CHEQUE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_SONI,
            ).count(),
            "/maintenance/paiements/",
            "info",
        )

    if role == "comptable_sogefi" or is_admin:
        _add_notification(
            items,
            "Cheques",
            Depense.objects.filter(statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE, mode_reglement=Depense.MODE_CHEQUE)
            .exclude(source_depense=Depense.SOURCE_GENERALE, entite_depense=Depense.ENTITE_SONI)
            .count(),
            "/maintenance/paiements/",
            "info",
        )

    if role == "comptable_avena" or is_admin:
        _add_notification(
            items,
            "Achat",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_AVENA,
            ).count(),
            "/depenses/?statut=attente_engagement_achat",
            "info",
        )
        _add_notification(
            items,
            "Cheques",
            Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
                mode_reglement=Depense.MODE_CHEQUE,
                source_depense=Depense.SOURCE_GENERALE,
                entite_depense=Depense.ENTITE_AVENA,
            ).count(),
            "/maintenance/paiements/",
            "warning",
        )

    if role in {"caissiere", "caissiere_soni", "caissiere_avena"} or is_admin:
        if is_admin:
            cash_count = Depense.objects.filter(
                statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
                mode_reglement=Depense.MODE_ESPECE,
            ).count()
        else:
            cash_count = _cash_depenses_queryset_for_role(Depense, role).count()
        _add_notification(items, "Caisse", cash_count, "/maintenance/paiements/", "danger")

    if role == "dga" or is_admin:
        _add_notification(items, "Cmd DGA", Commande.objects.filter(statut="attente_validation_dga").count(), "/commandes/?statut=attente_validation_dga", "warning")
        _add_notification(
            items,
            "Dep. maint/charg. SOGEFI",
            Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_CHARGEMENT_DGA).count(),
            "/depenses/?statut=attente_validation_chargement_dga",
            "warning",
            "Depenses BL et chargement issues du circuit logistique",
        )
        _add_notification(items, "Maint DGA", Maintenance.objects.filter(statut="attente_dga").count(), "/maintenance/garage/", "info")

    if role in {"dga_sogefi", "dga_avena"} or is_admin:
        entite = Depense.ENTITE_AVENA if role == "dga_avena" else Depense.ENTITE_SOGEFI
        label = "Dep. interne Avena" if role == "dga_avena" else "Dep. interne SOGEFI"
        detail = "Validation DGA depenses internes Avena" if role == "dga_avena" else "Validation DGA depenses internes SOGEFI"
        queryset = Depense.objects.filter(statut=Depense.STATUT_ATTENTE_VALIDATION_DGA, source_depense=Depense.SOURCE_GENERALE)
        if not is_admin:
            queryset = queryset.filter(entite_depense=entite)
        _add_notification(items, label, queryset.count(), "/depenses/?statut=attente_validation_dga_engagement", "warning", detail)

    if role == "directeur" or is_admin:
        _add_notification(items, "Cmd DG", Commande.objects.filter(statut="attente_validation_dg").count(), "/commandes/?statut=validee_dga", "danger")
        for notification in _depenses_dg_validation_breakdown(Depense):
            _add_notification(
                items,
                notification["label"],
                notification["count"],
                notification["url"],
                notification["tone"],
                notification["detail"],
            )
        _add_notification(items, "Maint DG", Maintenance.objects.filter(statut="attente_dg").count(), "/maintenance/garage/", "warning")

    if role == "responsable_achat" or is_admin:
        _add_notification(items, "Achats", Depense.objects.filter(statut=Depense.STATUT_ATTENTE_ENGAGEMENT).count(), "/depenses/?statut=attente_engagement_achat", "info")

    if role in {"commercial", "responsable_commercial"} or is_admin:
        if role in {"commercial", "responsable_commercial"} and not is_admin:
            commandes = Commande.objects.filter(statut="validee_dg").filter(
                Q(reference__isnull=True) | Q(reference="")
            )
        else:
            commandes = Commande.objects.filter(statut__in=["attente_validation_dga", "attente_validation_dg"])
        clients = Client.objects.all()
        if role == "commercial" and not is_admin:
            commandes = commandes.filter(client__commercial=user)
            clients = clients.filter(commercial=user)
        if role == "responsable_commercial" and not is_admin:
            commandes = commandes.filter(client__commercial__isnull=False)
        _add_notification(items, "Commandes", commandes.count(), "/commandes/", "info")
        _add_notification(
            items,
            "Risque",
            sum(1 for client in clients if client.niveau_risque in {"alerte", "critique"}),
            "/clients/?risque=a_risque",
            "warning",
        )

    return items[:6]


def user_access(request):
    context = build_user_permissions(request.user)
    if getattr(request.user, "is_authenticated", False):
        profil, _ = ProfilUtilisateur.objects.get_or_create(user=request.user)
        context["current_user_profile"] = profil
        context["current_user_initials"] = profil.initiales
        context["can_use_global_entity_selector"] = can_use_global_entity_selector(request.user)
        context["supervision_entity_choices"] = get_allowed_supervision_entities(request.user)
        context["active_supervision_entity"] = get_active_supervision_entity(request)
        context["active_supervision_entity_label"] = get_active_supervision_entity_label(request)
        active_entity = get_active_supervision_entity(request)
        context["sidebar_entity_mode"] = bool(can_use_global_entity_selector(request.user) and active_entity and active_entity != "all")
        context["sidebar_entity_is_soni"] = active_entity == "soni"
        context["sidebar_entity_is_sogefi"] = active_entity == "sogefi"
        context["sidebar_entity_is_avena"] = active_entity == "avena"
        notifications = _topbar_notifications(request.user, active_entity)
        context["topbar_notifications"] = notifications
        context["topbar_notifications_total"] = sum(item["count"] for item in notifications)
        context["topbar_messages_unread_total"] = MessageInterne.objects.filter(destinataire=request.user, lu=False).count()
        context["topbar_recent_messages"] = (
            MessageInterne.objects.select_related("expediteur", "expediteur__profil_utilisateur")
            .filter(destinataire=request.user)
            .order_by("-created_at")[:5]
        )
    else:
        context["current_user_profile"] = None
        context["current_user_initials"] = ""
        context["can_use_global_entity_selector"] = False
        context["supervision_entity_choices"] = []
        context["active_supervision_entity"] = ""
        context["active_supervision_entity_label"] = ""
        context["sidebar_entity_mode"] = False
        context["sidebar_entity_is_soni"] = False
        context["sidebar_entity_is_sogefi"] = False
        context["sidebar_entity_is_avena"] = False
        context["topbar_notifications"] = []
        context["topbar_notifications_total"] = 0
        context["topbar_messages_unread_total"] = 0
        context["topbar_recent_messages"] = []
    return context
