from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from depenses.models import Depense, TypeDepense
from maintenance.models import Fournisseur


class DepenseDgaAccessTests(TestCase):
    def setUp(self):
        self.dga_group, _created = Group.objects.get_or_create(name="dga")
        self.logistique_group, _created = Group.objects.get_or_create(name="logistique")
        self.caissiere_soni_group, _created = Group.objects.get_or_create(name="caissiere_soni")
        self.dga_user = User.objects.create_user(
            username="dga_soni",
            password="testpass123",
        )
        self.dga_user.groups.add(self.dga_group)
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
        )
        self.fournisseur_interne = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur Test",
            entreprise="Entreprise Test",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
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
