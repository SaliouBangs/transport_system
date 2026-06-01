from django.conf import settings
from django.db import models
from django.utils.text import slugify


def photo_profil_upload_path(instance, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    username = slugify(instance.user.username) or f"user-{instance.user_id}"
    return f"profils/{instance.user_id}/{username}.{extension}"


class ProfilUtilisateur(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profil_utilisateur",
    )
    photo = models.FileField(upload_to=photo_profil_upload_path, blank=True)
    enabled_permissions = models.JSONField(default=list, blank=True)
    disabled_permissions = models.JSONField(default=list, blank=True)
    readonly_permissions = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"

    def __str__(self):
        return f"Profil de {self.user.username}"

    @property
    def photo_url(self):
        if self.photo:
            return self.photo.url
        return ""

    @property
    def initiales(self):
        first_name = (self.user.first_name or "").strip()
        last_name = (self.user.last_name or "").strip()
        if first_name or last_name:
            return f"{first_name[:1]}{last_name[:1]}".upper()
        return (self.user.username[:2] or "U").upper()


class HistoriqueAction(models.Model):
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="historique_actions",
    )
    module = models.CharField(max_length=100)
    action = models.CharField(max_length=120)
    cible = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Historique d'action"
        verbose_name_plural = "Historique des actions"

    def __str__(self):
        utilisateur = self.utilisateur.username if self.utilisateur else "Systeme"
        return f"{utilisateur} - {self.action}"


def journaliser_action(utilisateur, module, action, cible="", description=""):
    if not getattr(utilisateur, "is_authenticated", False):
        utilisateur = None

    return HistoriqueAction.objects.create(
        utilisateur=utilisateur,
        module=module,
        action=action,
        cible=cible or "",
        description=description or "",
    )


class MessageInterne(models.Model):
    expediteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages_envoyes",
    )
    destinataire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="messages_recus",
    )
    titre = models.CharField(max_length=160)
    contenu = models.TextField()
    lien = models.CharField(max_length=255, blank=True)
    lu = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Message interne"
        verbose_name_plural = "Messages internes"

    def __str__(self):
        return f"{self.titre} -> {self.destinataire.username}"

    @property
    def lien_normalise(self):
        lien = (self.lien or "").strip()
        if not lien:
            return "/comptes/messages/"
        if lien.startswith(("http://", "https://", "/")):
            return lien
        return f"/{lien}"


def envoyer_message_interne(expediteur, destinataires, titre, contenu, lien=""):
    lien = (lien or "").strip()
    if lien and not lien.startswith(("http://", "https://", "/")):
        lien = f"/{lien}"
    messages = []
    for destinataire in destinataires:
        if not destinataire or not getattr(destinataire, "is_active", False):
            continue
        messages.append(
            MessageInterne(
                expediteur=expediteur if getattr(expediteur, "is_authenticated", False) else None,
                destinataire=destinataire,
                titre=titre,
                contenu=contenu,
                lien=lien or "",
            )
        )
    return MessageInterne.objects.bulk_create(messages)
