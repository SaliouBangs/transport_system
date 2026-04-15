from django.conf import settings
from django.db import models


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

# Create your models here.
