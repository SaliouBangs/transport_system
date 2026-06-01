import shutil
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse


TEST_MEDIA_ROOT = Path(__file__).resolve().parents[1] / "test_media" / "utilisateurs"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ProfilUtilisateurTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if TEST_MEDIA_ROOT.exists():
            shutil.rmtree(TEST_MEDIA_ROOT)

    def setUp(self):
        self.user = User.objects.create_user(
            username="amina",
            password="AncienPass123!",
            first_name="Amina",
            last_name="Diallo",
            email="amina@example.com",
        )

    def test_profil_requires_authentication(self):
        response = self.client.get(reverse("profil"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/comptes/connexion/", response["Location"])

    def test_user_can_update_own_profile(self):
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.post(
            reverse("profil"),
            {
                "modifier_profil": "1",
                "username": "amina2",
                "first_name": "Aminata",
                "last_name": "Camara",
                "email": "aminata@example.com",
            },
        )

        self.assertRedirects(response, reverse("profil"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "amina2")
        self.assertEqual(self.user.first_name, "Aminata")
        self.assertEqual(self.user.last_name, "Camara")
        self.assertEqual(self.user.email, "aminata@example.com")

    def test_user_can_change_password_and_stay_logged_in(self):
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.post(
            reverse("profil"),
            {
                "modifier_mot_de_passe": "1",
                "old_password": "AncienPass123!",
                "new_password1": "NouveauPass123!",
                "new_password2": "NouveauPass123!",
            },
        )

        self.assertRedirects(response, reverse("profil"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NouveauPass123!"))
        response = self.client.get(reverse("profil"))
        self.assertEqual(response.status_code, 200)

    def test_user_can_upload_profile_photo(self):
        self.client.login(username="amina", password="AncienPass123!")
        photo = SimpleUploadedFile(
            "avatar.png",
            b"image-content",
            content_type="image/png",
        )

        response = self.client.post(
            reverse("profil"),
            {
                "modifier_profil": "1",
                "username": "amina",
                "first_name": "Amina",
                "last_name": "Diallo",
                "email": "amina@example.com",
                "photo": photo,
            },
        )

        self.assertRedirects(response, reverse("profil"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.profil_utilisateur.photo.name.startswith("profils/"))

    def test_notifications_status_returns_json(self):
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.get(reverse("notifications_status"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("total", payload)
        self.assertIn("notifications", payload)
