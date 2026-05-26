from django.contrib import admin

from .models import DemandeNouveauBL, Operation, Produit


admin.site.register(Produit)
admin.site.register(Operation)


@admin.register(DemandeNouveauBL)
class DemandeNouveauBLAdmin(admin.ModelAdmin):
    list_display = ("ancienne_operation", "commande", "nouveau_camion", "statut", "created_at")
    list_filter = ("statut", "created_at")
    search_fields = ("ancienne_operation__numero_bl", "commande__reference", "nouveau_camion__numero_tracteur")
