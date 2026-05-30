from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from clients.forms import ClientDestinationForm
from clients.models import VillePerequation


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
