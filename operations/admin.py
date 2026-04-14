from django.contrib import admin

from .models import Operation, Produit


admin.site.register(Produit)
admin.site.register(Operation)
