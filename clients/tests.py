from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from clients.forms import ClientDestinationForm
from clients.models import Client, EncaissementClient, EncaissementClientAllocation, VillePerequation
from clients.views import _build_rapport_encaissements_commerciaux_context
from commandes.models import Commande
from operations.models import Produit


class VillePerequationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_test",
            email="admin@example.com",
            password="testpass123",
        )

    def test_admin_peut_creer_une_ville_de_perequation_depuis_parametres(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("villes_perequation"),
            {
                "nom": "Boke",
                "tarif_gnf_litre": "286.69",
                "actif": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        ville = VillePerequation.objects.get(nom="Boke")
        self.assertEqual(str(ville.tarif_gnf_litre), "286.69")
        self.assertTrue(ville.actif)


class ClientDestinationFormTests(TestCase):
    def test_ville_perequation_est_optionnelle(self):
        form = ClientDestinationForm(data={"adresse": "BOFFA", "ville_perequation": ""})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["ville_perequation"])


class RapportEncaissementsCommerciauxTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_superuser(
            username="admin_rapport",
            email="rapport@example.com",
            password="testpass123",
        )
        self.commercial = User.objects.create_user(
            username="commercial_rapport",
            first_name="Fanta",
            last_name="Barry",
        )
        self.client_obj = Client.objects.create(
            commercial=self.commercial,
            nom="Client test",
            telephone="620000000",
            entreprise="CLIENT RAPPORT",
            ville="Conakry",
        )
        self.essence = Produit.objects.create(nom="ESSENCE")
        self.commande = Commande.objects.create(
            reference="CC-001",
            client=self.client_obj,
            ville_depart="CONAKRY",
            ville_arrivee="KINDIA",
            date_livraison_prevue=timezone.localdate(),
            produit=self.essence,
            quantite=Decimal("1000"),
            prix_negocie=Decimal("10000"),
        )

    def _context(self):
        request = self.factory.get("/clients/rapports/encaissements-commerciaux/")
        request.user = self.admin
        request.session = {}
        return _build_rapport_encaissements_commerciaux_context(request)

    def test_rapport_inclut_avances_et_solde_initial_dans_autres(self):
        EncaissementClient.objects.create(
            client=self.client_obj,
            type_encaissement="avance_client",
            date_encaissement=timezone.localdate(),
            montant=Decimal("1500000"),
            mode_paiement="cheque",
        )
        EncaissementClient.objects.create(
            client=self.client_obj,
            type_encaissement="solde_initial",
            date_encaissement=timezone.localdate(),
            montant=Decimal("500000"),
            mode_paiement="virement",
        )

        context = self._context()

        self.assertEqual(context["totals"]["essence"], Decimal("0.00"))
        self.assertEqual(context["totals"]["other"], Decimal("2000000.00"))
        self.assertEqual(context["totals"]["global"], Decimal("2000000.00"))
        self.assertEqual(len(context["payment_rows"]), 2)

    def test_rapport_ne_perd_pas_le_reste_non_affecte_d_une_avance(self):
        encaissement = EncaissementClient.objects.create(
            client=self.client_obj,
            type_encaissement="avance_client",
            date_encaissement=timezone.localdate(),
            montant=Decimal("3000000"),
            mode_paiement="cheque",
        )
        EncaissementClientAllocation.objects.create(
            encaissement=encaissement,
            cible_type="commande",
            commande=self.commande,
            montant_affecte=Decimal("1200000"),
        )

        context = self._context()

        self.assertEqual(context["totals"]["essence"], Decimal("1200000.00"))
        self.assertEqual(context["totals"]["other"], Decimal("1800000.00"))
        self.assertEqual(context["totals"]["global"], Decimal("3000000.00"))
