from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from camions.models import Camion
from chauffeurs.models import Chauffeur
from clients.models import Client
from commandes.models import Commande

from .models import DemandeNouveauBL, HistoriqueAffectationOperation, Operation, Produit


class DemandeNouveauBLTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="AdminPass123!",
        )
        self.client.login(username="admin", password="AdminPass123!")
        self.produit = Produit.objects.create(nom="GASOIL")
        self.client_obj = Client.objects.create(
            nom="Mamadou",
            telephone="620000000",
            entreprise="Client Test",
            ville="Conakry",
        )
        self.ancien_camion = Camion.objects.create(numero_tracteur="TR-001", capacite=30000)
        self.nouveau_camion = Camion.objects.create(numero_tracteur="TR-002", capacite=30000)
        self.ancien_chauffeur = Chauffeur.objects.create(nom="Ancien Chauffeur", telephone="620000001", camion=self.ancien_camion)
        self.nouveau_chauffeur = Chauffeur.objects.create(nom="Nouveau Chauffeur", telephone="620000002", camion=self.nouveau_camion)
        self.commande = Commande.objects.create(
            reference="CMD-001",
            client=self.client_obj,
            ville_depart="Conakry",
            ville_arrivee="Kankan",
            date_livraison_prevue="2026-05-30",
            statut="planifiee",
            produit=self.produit,
            camion=self.nouveau_camion,
            chauffeur=self.nouveau_chauffeur,
            quantite=Decimal("30000.00"),
            prix_negocie=Decimal("1000.00"),
        )
        self.operation = Operation.objects.create(
            numero_bl="BL-001",
            etat_bon="liquide",
            commande=self.commande,
            client=self.client_obj,
            destination="Kankan",
            camion=self.ancien_camion,
            chauffeur=self.ancien_chauffeur,
            produit=self.produit,
            quantite=Decimal("30000.00"),
            stock_sommier_deduit=True,
        )
        self.demande = DemandeNouveauBL.objects.create(
            ancienne_operation=self.operation,
            commande=self.commande,
            nouveau_camion=self.nouveau_camion,
            nouveau_chauffeur=self.nouveau_chauffeur,
            cree_par=self.user,
        )

    def test_comptable_creates_new_bl_from_truck_change_request(self):
        response = self.client.post(reverse("creer_bl_depuis_demande", args=[self.demande.id]))

        self.assertRedirects(response, reverse("comptable_operations"))
        self.operation.refresh_from_db()
        self.demande.refresh_from_db()
        nouvelle_operation = self.demande.nouvelle_operation

        self.assertEqual(self.operation.numero_bl, "BL-001-ANCIEN")
        self.assertEqual(self.operation.remplace_par, nouvelle_operation)
        self.assertEqual(nouvelle_operation.numero_bl, "BL-001")
        self.assertEqual(nouvelle_operation.etat_bon, "initie")
        self.assertEqual(nouvelle_operation.camion, self.nouveau_camion)
        self.assertEqual(nouvelle_operation.chauffeur, self.nouveau_chauffeur)
        self.assertEqual(self.demande.statut, DemandeNouveauBL.STATUT_TRAITEE)
        self.assertTrue(
            HistoriqueAffectationOperation.objects.filter(
                operation=nouvelle_operation,
                ancien_camion=self.ancien_camion,
                nouveau_camion=self.nouveau_camion,
            ).exists()
        )
