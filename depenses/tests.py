from pathlib import Path

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from depenses.forms import DepenseEngagementForm
from depenses.models import Depense, DepenseJustificatif, TypeDepense
from depenses.models import LieuProjet
from maintenance.models import Fournisseur


TEST_MEDIA_ROOT = Path(__file__).resolve().parents[1] / "test_media"


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class DepenseDgaAccessTests(TestCase):
    def setUp(self):
        self.dga_group, _created = Group.objects.get_or_create(name="dga")
        self.dga_avena_group, _created = Group.objects.get_or_create(name="dga_avena")
        self.logistique_group, _created = Group.objects.get_or_create(name="logistique")
        self.caissiere_soni_group, _created = Group.objects.get_or_create(name="caissiere_soni")
        self.caissiere_avena_group, _created = Group.objects.get_or_create(name="caissiere_avena")
        self.comptable_avena_group, _created = Group.objects.get_or_create(name="comptable_avena")
        self.dga_user = User.objects.create_user(
            username="dga_soni",
            password="testpass123",
        )
        self.dga_user.groups.add(self.dga_group)
        self.dga_avena_user = User.objects.create_user(
            username="dga_avena",
            password="testpass123",
        )
        self.dga_avena_user.groups.add(self.dga_avena_group)
        self.logistique_user = User.objects.create_user(
            username="log_soni",
            password="testpass123",
        )
        self.logistique_user.groups.add(self.logistique_group)
        self.caissiere_soni_user = User.objects.create_user(
            username="caisse_soni",
            password="testpass123",
        )
        self.caissiere_soni_user.groups.add(self.caissiere_soni_group)
        self.caissiere_avena_user = User.objects.create_user(
            username="caisse_avena",
            password="testpass123",
        )
        self.caissiere_avena_user.groups.add(self.caissiere_avena_group)
        self.comptable_avena_user = User.objects.create_user(
            username="compta_avena",
            password="testpass123",
        )
        self.comptable_avena_user.groups.add(self.comptable_avena_group)

        self.depense_interne = Depense.objects.create(
            demandeur=self.dga_user,
            titre="Depense interne SOGEFI",
            description="Depense interne a masquer pour le DGA SONI.",
            source_depense=Depense.SOURCE_GENERALE,
            statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
        )
        self.type_depense_interne = TypeDepense.objects.create(
            libelle="Fournitures internes",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_SOGEFI,
        )
        self.fournisseur_interne = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur Test",
            entreprise="Entreprise Test",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
            entite_reference=Fournisseur.ENTITE_SOGEFI,
        )
        self.type_depense_soni = TypeDepense.objects.create(
            libelle="Internet SONI",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_SONI,
        )
        self.lieu_soni = LieuProjet.objects.create(
            libelle="Depot SONI Test",
            entite_reference=TypeDepense.ENTITE_SONI,
        )
        self.fournisseur_soni = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur SONI",
            entreprise="Entreprise SONI",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
            entite_reference=Fournisseur.ENTITE_SONI,
        )
        self.fournisseur_soni_logistique = Fournisseur.objects.create(
            nom_fournisseur="DZD",
            entreprise="DZD Telecom",
            portefeuille=Fournisseur.PORTEFEUILLE_LOGISTIQUE,
            entite_reference=Fournisseur.ENTITE_SONI,
        )
        self.type_depense_avena = TypeDepense.objects.create(
            libelle="Fournitures Avena",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_AVENA,
        )
        self.lieu_avena = LieuProjet.objects.create(
            libelle="Bureau Avena",
            entite_reference=TypeDepense.ENTITE_AVENA,
        )
        self.fournisseur_avena = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur Avena",
            entreprise="Entreprise Avena",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
            entite_reference=Fournisseur.ENTITE_AVENA,
        )

    def test_dga_ne_voit_pas_les_depenses_internes_dans_la_liste(self):
        self.client.force_login(self.dga_user)

        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.depense_interne.reference)

    def test_dga_ne_peut_pas_modifier_une_depense_interne_par_url_directe(self):
        self.client.force_login(self.dga_user)

        response = self.client.get(reverse("modifier_depense", args=[self.depense_interne.id]))

        self.assertEqual(response.status_code, 404)

    def test_logistique_cree_une_depense_interne_soni(self):
        self.client.force_login(self.logistique_user)

        response = self.client.post(
            reverse("ajouter_depense"),
            {
                "titre": "Achat interne SONI",
                "date_expression": "2026-05-23",
                "description": "Besoin interne SONI.",
                "ligne_designation[]": ["Pompe"],
                "ligne_quantite[]": ["2"],
            },
        )

        self.assertEqual(response.status_code, 302)
        depense = Depense.objects.get(titre="Achat interne SONI")
        self.assertEqual(depense.entite_depense, Depense.ENTITE_SONI)
        self.assertEqual(depense.source_depense, Depense.SOURCE_GENERALE)

    def test_dga_voit_les_depenses_internes_soni(self):
        depense_soni = Depense.objects.create(
            demandeur=self.logistique_user,
            titre="Depense interne SONI",
            description="Depense interne visible par le DGA SONI.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
        )

        self.client.force_login(self.dga_user)
        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_soni.reference)

    def test_caissiere_soni_ne_voit_que_les_depenses_especes_soni(self):
        depense_soni = Depense.objects.create(
            demandeur=self.logistique_user,
            titre="Paiement espece SONI",
            description="Depense espece SONI.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            type_depense=self.type_depense_interne,
            lieu_ou_projet="Depot SONI",
            montant_engage="150000",
            fournisseur=self.fournisseur_interne,
        )
        depense_sogefi = Depense.objects.create(
            demandeur=self.dga_user,
            titre="Paiement espece SOGEFI",
            description="Depense espece SOGEFI.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SOGEFI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            type_depense=self.type_depense_interne,
            lieu_ou_projet="Siege SOGEFI",
            montant_engage="125000",
            fournisseur=self.fournisseur_interne,
        )

        self.client.force_login(self.caissiere_soni_user)
        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_soni.reference)
        self.assertNotContains(response, depense_sogefi.reference)

    def test_formulaire_engagement_soni_ne_charge_que_les_references_soni(self):
        depense_soni = Depense.objects.create(
            demandeur=self.logistique_user,
            titre="Depense engagement SONI",
            description="Depense SONI pour test de references.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
        )

        form = DepenseEngagementForm(instance=depense_soni, entite_reference=Depense.ENTITE_SONI)

        self.assertIn(self.type_depense_soni, form.type_depenses)
        self.assertNotIn(self.type_depense_interne, form.type_depenses)
        self.assertIn(self.lieu_soni, form.lieux_projets)
        self.assertNotIn(self.fournisseur_interne, form.fournisseurs)
        self.assertIn(self.fournisseur_soni_logistique, form.fournisseurs)

    def test_comptable_avena_voit_les_depenses_avena_a_engager(self):
        depense_avena = Depense.objects.create(
            demandeur=self.dga_avena_user,
            titre="Depense Avena",
            description="Depense interne Avena a saisir.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
        )

        self.client.force_login(self.comptable_avena_user)
        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_avena.reference)

    def test_comptable_avena_peut_creer_une_depense_avena(self):
        self.client.force_login(self.comptable_avena_user)

        response = self.client.post(
            reverse("ajouter_depense"),
            {
                "titre": "Nouvelle depense Avena",
                "date_expression": "2026-06-01",
                "description": "Besoin interne Avena.",
                "ligne_designation[]": ["Fournitures bureau"],
                "ligne_quantite[]": ["3"],
            },
        )

        self.assertEqual(response.status_code, 302)
        depense = Depense.objects.get(titre="Nouvelle depense Avena")
        self.assertEqual(depense.entite_depense, Depense.ENTITE_AVENA)
        self.assertEqual(depense.source_depense, Depense.SOURCE_GENERALE)
        self.assertEqual(depense.statut, Depense.STATUT_ATTENTE_ENGAGEMENT)

    def test_comptable_avena_voit_le_bouton_nouvelle_depense(self):
        self.client.force_login(self.comptable_avena_user)

        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nouvelle depense interne")

    def test_dga_avena_valide_les_depenses_avena(self):
        depense_avena = Depense.objects.create(
            demandeur=self.dga_avena_user,
            titre="Validation Avena",
            description="Validation DGA Avena.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_VALIDATION_DGA,
            type_depense=self.type_depense_avena,
            lieu_ou_projet="Bureau Avena",
            montant_engage="120000",
            fournisseur=self.fournisseur_avena,
        )

        self.client.force_login(self.dga_avena_user)
        response = self.client.post(reverse("valider_engagement_dga", args=[depense_avena.id]))

        self.assertEqual(response.status_code, 302)
        depense_avena.refresh_from_db()
        self.assertEqual(depense_avena.statut, Depense.STATUT_ATTENTE_VALIDATION_DG)
        self.assertEqual(depense_avena.validation_dga_par, self.dga_avena_user)

    def test_caissiere_avena_ne_voit_que_les_depenses_especes_avena(self):
        depense_avena = Depense.objects.create(
            demandeur=self.dga_avena_user,
            titre="Paiement espece Avena",
            description="Depense espece Avena.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            type_depense=self.type_depense_avena,
            lieu_ou_projet="Bureau Avena",
            montant_engage="95000",
            fournisseur=self.fournisseur_avena,
        )
        depense_soni = Depense.objects.create(
            demandeur=self.logistique_user,
            titre="Paiement espece SONI 2",
            description="Depense espece SONI.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            type_depense=self.type_depense_soni,
            lieu_ou_projet="Depot SONI",
            montant_engage="150000",
            fournisseur=self.fournisseur_soni,
        )

        self.client.force_login(self.caissiere_avena_user)
        response = self.client.get(reverse("liste_depenses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_avena.reference)
        self.assertNotContains(response, depense_soni.reference)

    def test_lieu_projet_peut_avoir_le_meme_libelle_dans_deux_entites(self):
        lieu_sogefi = LieuProjet.objects.create(
            libelle="BUREAU",
            entite_reference=TypeDepense.ENTITE_SOGEFI,
        )
        lieu_soni = LieuProjet.objects.create(
            libelle="BUREAU",
            entite_reference=TypeDepense.ENTITE_SONI,
        )

        self.assertNotEqual(lieu_sogefi.id, lieu_soni.id)
        self.assertEqual(LieuProjet.objects.filter(libelle="BUREAU").count(), 2)

    def test_type_depense_interne_peut_avoir_le_meme_libelle_dans_deux_entites(self):
        type_sogefi = TypeDepense.objects.create(
            libelle="Internet",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_SOGEFI,
        )
        type_soni = TypeDepense.objects.create(
            libelle="Internet",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_SONI,
        )

        self.assertNotEqual(type_sogefi.id, type_soni.id)
        self.assertEqual(TypeDepense.objects.filter(libelle="Internet", portefeuille=TypeDepense.PORTEFEUILLE_INTERNE).count(), 2)

    def test_engagement_accepte_plusieurs_pieces_justificatives(self):
        depense_soni = Depense.objects.create(
            demandeur=self.logistique_user,
            titre="Depense multi justificatifs SONI",
            description="Depense SONI avec deux factures photo.",
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SONI,
            statut=Depense.STATUT_ATTENTE_ENGAGEMENT,
        )

        self.client.force_login(self.logistique_user)
        response = self.client.post(
            reverse("engagement_depense", args=[depense_soni.id]),
            {
                "type_depense": str(self.type_depense_soni.id),
                "type_depense_search": self.type_depense_soni.libelle,
                "lieu_projet_ref": str(self.lieu_soni.id),
                "lieu_ou_projet": self.lieu_soni.libelle,
                "lieu_ou_projet_search": self.lieu_soni.libelle,
                "fournisseur": str(self.fournisseur_soni.id),
                "fournisseur_search": str(self.fournisseur_soni),
                "numero_facture": "FAC-SONI-001",
                "engagement_observation": "Deux pieces ajoutees.",
                "ligne_designation[]": ["Routeur", "Cables"],
                "ligne_quantite[]": ["1", "3"],
                "ligne_prix[]": ["250000", "15000"],
                "pieces_justificatives": [
                    SimpleUploadedFile("facture1.jpg", b"filecontent1", content_type="image/jpeg"),
                    SimpleUploadedFile("facture2.jpg", b"filecontent2", content_type="image/jpeg"),
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        depense_soni.refresh_from_db()
        self.assertEqual(depense_soni.pieces_justificatives.count(), 2)
        self.assertTrue(depense_soni.piece_justificative)
        self.assertEqual(DepenseJustificatif.objects.filter(depense=depense_soni).count(), 2)
