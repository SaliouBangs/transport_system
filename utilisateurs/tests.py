import shutil
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from utilisateurs.models import MessageInterne


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
        expediteur = User.objects.create_user(username="dg_msg", first_name="DG", last_name="Test")
        MessageInterne.objects.create(
            expediteur=expediteur,
            destinataire=self.user,
            titre="Validation",
            contenu="Merci de verifier ce dossier.",
        )

        response = self.client.get(reverse("notifications_status"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("total", payload)
        self.assertIn("notifications", payload)
        self.assertEqual(payload["messages_unread"], 1)
        self.assertEqual(payload["latest_message"]["expediteur"], "DG Test")

    def test_user_can_send_internal_message_to_multiple_users(self):
        sender = User.objects.create_user(username="admin_msg", password="AdminPass123!", is_staff=True)
        other_user = User.objects.create_user(username="ousmane", password="Pass12345!")
        self.client.login(username="admin_msg", password="AdminPass123!")

        response = self.client.post(
            reverse("messages_internes"),
            {
                "action": "send",
                "destinataires": [str(self.user.id), str(other_user.id)],
                "titre": "Commande a valider",
                "contenu": "Merci de traiter la commande.",
            },
        )

        self.assertRedirects(response, reverse("messages_internes"))
        self.assertEqual(MessageInterne.objects.filter(titre="Commande a valider").count(), 2)

    def test_internal_message_link_is_normalized(self):
        from utilisateurs.models import envoyer_message_interne

        envoyer_message_interne(self.user, [self.user], "Voir commandes", "Ouvre la page commandes.", "commandes/")
        message = MessageInterne.objects.get(destinataire=self.user)
        self.assertEqual(message.lien, "/commandes/")
        self.assertEqual(message.lien_normalise, "/commandes/")

    def test_user_can_mark_internal_messages_as_read(self):
        MessageInterne.objects.create(destinataire=self.user, titre="Rappel", contenu="A traiter")
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.post(reverse("messages_internes"), {"action": "mark_read"})

        self.assertRedirects(response, reverse("messages_internes"))
        self.assertFalse(MessageInterne.objects.filter(destinataire=self.user, lu=False).exists())

    def test_messages_page_shows_mobile_recipient_search(self):
        MessageInterne.objects.create(
            expediteur=self.user,
            destinataire=User.objects.create_user(username="dest_msg", password="Pass12345!"),
            titre="Message envoye",
            contenu="Suivi",
        )
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.get(reverse("messages_internes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "recipient-search")
        self.assertContains(response, "Messages envoyes")
        self.assertContains(response, "Message envoye")
        self.assertNotContains(response, "Choisir un role")
        self.assertNotContains(response, "Lien vers la tache")

    def test_messages_page_shows_reply_button_with_sender(self):
        sender = User.objects.create_user(username="dg_reply", password="Pass12345!", first_name="DG")
        MessageInterne.objects.create(
            expediteur=sender,
            destinataire=self.user,
            titre="Confirmation validation",
            contenu="J'ai valide deja.",
        )
        self.client.login(username="amina", password="AncienPass123!")

        response = self.client.get(reverse("messages_internes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Repondre")
        self.assertContains(response, f'data-reply-user-id="{sender.id}"')
        self.assertContains(response, 'id="message-title"')
        self.assertContains(response, 'id="message-content"')
