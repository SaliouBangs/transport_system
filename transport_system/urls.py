from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import path, include
from utilisateurs import views as utilisateurs_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', utilisateurs_views.home_redirect, name='home'),
    path('dashboard/', include('dashboard.urls')),
    path('comptes/', include('utilisateurs.urls')),
    path('camions/', include('camions.urls')),
    path('chauffeurs/', include('chauffeurs.urls')),
    path('prospects/', include('prospects.urls')),
    path('clients/', include('clients.urls')),
    path('commandes/', include('commandes.urls')),
    path('maintenance/', include('maintenance.urls')),
    path('documents/', include('documents.urls')),
    path('operations/', include('operations.urls')),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
