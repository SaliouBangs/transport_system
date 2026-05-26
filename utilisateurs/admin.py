from django.contrib import admin

from .models import HistoriqueAction, ProfilUtilisateur


@admin.register(ProfilUtilisateur)
class ProfilUtilisateurAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    fields = ("user", "photo", "enabled_permissions", "disabled_permissions", "readonly_permissions", "updated_at")
    readonly_fields = ("updated_at",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email")


admin.site.register(HistoriqueAction)
