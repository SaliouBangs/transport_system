from django.test import TestCase

from clients.models import Client, ClientDestinationAdresse, VillePerequation
from commandes.forms import CommandeForm
from operations.models import Produit


class CommandePerequationTests(TestCase):
    def setUp(self):
        self.client_instance = Client.objects.create(
            nom="Client Test",
            telephone="620000000",
            entreprise="Client Perequation",
            ville="Conakry",
        )
        self.ville_perequation = VillePerequation.objects.create(
            nom="Boke",
            tarif_gnf_litre="286.69",
            actif=True,
        )
        ClientDestinationAdresse.objects.create(
            client=self.client_instance,
            adresse="Kamsar",
            ville_perequation=self.ville_perequation,
        )
        self.produit = Produit.objects.create(nom="ESSENCE")

    def test_commande_recupere_la_perequation_depuis_la_destination_client(self):
        form = CommandeForm(
            data={
                "client": self.client_instance.id,
                "description": "Commande test",
                "ville_depart": "CONAKRY",
                "ville_arrivee": "Kamsar",
                "date_livraison_prevue": "2026-05-23",
                "delai_paiement_jours": "15",
                "produit": self.produit.id,
                "quantite": "12000",
                "prix_negocie": "4500",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        commande = form.save()
        self.assertEqual(commande.ville_perequation, self.ville_perequation)
        self.assertEqual(str(commande.tarif_perequation_gnf_litre), "286.69")

    def test_commande_refuse_une_destination_non_enregistree_pour_le_client(self):
        form = CommandeForm(
            data={
                "client": self.client_instance.id,
                "description": "Commande test",
                "ville_depart": "CONAKRY",
                "ville_arrivee": "Mamou",
                "date_livraison_prevue": "2026-05-23",
                "delai_paiement_jours": "15",
                "produit": self.produit.id,
                "quantite": "12000",
                "prix_negocie": "4500",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("ville_arrivee", form.errors)

    def test_commande_accepte_une_destination_sans_perequation(self):
        ClientDestinationAdresse.objects.create(
            client=self.client_instance,
            adresse="Kindia Dougueta",
            ville_perequation=None,
        )
        form = CommandeForm(
            data={
                "client": self.client_instance.id,
                "description": "Commande sans perequation",
                "ville_depart": "CONAKRY",
                "ville_arrivee": "Kindia Dougueta",
                "date_livraison_prevue": "2026-05-23",
                "delai_paiement_jours": "15",
                "produit": self.produit.id,
                "quantite": "12000",
                "prix_negocie": "4500",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        commande = form.save()
        self.assertEqual(commande.ville_arrivee, "Kindia Dougueta")
        self.assertIsNone(commande.ville_perequation)
        self.assertIsNone(commande.tarif_perequation_gnf_litre)

    def test_commande_refuse_une_quantite_superieure_a_40000_litres(self):
        form = CommandeForm(
            data={
                "client": self.client_instance.id,
                "description": "Commande test",
                "ville_depart": "CONAKRY",
                "ville_arrivee": "Kamsar",
                "date_livraison_prevue": "2026-05-23",
                "delai_paiement_jours": "15",
                "produit": self.produit.id,
                "quantite": "40001",
                "prix_negocie": "4500",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("quantite", form.errors)
        self.assertIn("40 000 L", form.errors["quantite"][0])
