from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from depenses.models import Depense, TypeDepense
from maintenance.models import ApprovisionnementCaisse, Fournisseur, PanneCatalogue, PanneFournisseurPrix, TypeMaintenance
from maintenance.views import _get_caisse_metrics, _get_dg_metrics


class PaiementsAvenaIsolationTests(TestCase):
    def setUp(self):
        self.comptable_avena_group, _ = Group.objects.get_or_create(name="comptable_avena")
        self.caissiere_avena_group, _ = Group.objects.get_or_create(name="caissiere_avena")

        self.comptable_avena = User.objects.create_user(
            username="conde",
            password="testpass123",
        )
        self.comptable_avena.groups.add(self.comptable_avena_group)

        self.caissiere_avena = User.objects.create_user(
            username="oumou",
            password="testpass123",
        )
        self.caissiere_avena.groups.add(self.caissiere_avena_group)

        self.fournisseur_avena = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur Avena",
            entreprise="Entreprise Avena",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
            entite_reference=Fournisseur.ENTITE_AVENA,
        )
        self.fournisseur_sogefi = Fournisseur.objects.create(
            nom_fournisseur="Fournisseur SOGEFI",
            entreprise="Entreprise SOGEFI",
            portefeuille=Fournisseur.PORTEFEUILLE_INTERNE,
            entite_reference=Fournisseur.ENTITE_SOGEFI,
        )
        self.type_depense_avena = TypeDepense.objects.create(
            libelle="Type Avena",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_AVENA,
        )
        self.type_depense_sogefi = TypeDepense.objects.create(
            libelle="Type SOGEFI",
            portefeuille=TypeDepense.PORTEFEUILLE_INTERNE,
            entite_reference=TypeDepense.ENTITE_SOGEFI,
        )

    def test_comptable_avena_ne_voit_que_les_paiements_cheque_avena(self):
        depense_avena = Depense.objects.create(
            titre="Achat Avena",
            description="Depense interne Avena",
            demandeur=self.comptable_avena,
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            mode_reglement=Depense.MODE_CHEQUE,
            montant_engage="250000",
            type_depense=self.type_depense_avena,
            lieu_ou_projet="Bureau Avena",
            fournisseur=self.fournisseur_avena,
        )
        depense_sogefi = Depense.objects.create(
            titre="Achat SOGEFI",
            description="Depense interne SOGEFI",
            demandeur=self.comptable_avena,
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SOGEFI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_COMPTABLE,
            mode_reglement=Depense.MODE_CHEQUE,
            montant_engage="500000",
            type_depense=self.type_depense_sogefi,
            lieu_ou_projet="Siege SOGEFI",
            fournisseur=self.fournisseur_sogefi,
        )

        self.client.force_login(self.comptable_avena)
        response = self.client.get(reverse("paiements_maintenances"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_avena.reference)
        self.assertNotContains(response, depense_sogefi.reference)
        self.assertNotContains(response, "Depense BL")
        self.assertContains(response, "Comptable Avena")
        self.assertNotContains(response, "Comptable SOGEFI")

    def test_caissiere_avena_ne_voit_que_les_paiements_espece_avena(self):
        depense_avena = Depense.objects.create(
            titre="Caisse Avena",
            description="Depense espece Avena",
            demandeur=self.caissiere_avena,
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_AVENA,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            montant_engage="350000",
            type_depense=self.type_depense_avena,
            lieu_ou_projet="Bureau Avena",
            fournisseur=self.fournisseur_avena,
        )
        depense_sogefi = Depense.objects.create(
            titre="Caisse SOGEFI",
            description="Depense espece SOGEFI",
            demandeur=self.caissiere_avena,
            source_depense=Depense.SOURCE_GENERALE,
            entite_depense=Depense.ENTITE_SOGEFI,
            statut=Depense.STATUT_ATTENTE_PAIEMENT_CAISSIERE,
            mode_reglement=Depense.MODE_ESPECE,
            montant_engage="450000",
            type_depense=self.type_depense_sogefi,
            lieu_ou_projet="Siege SOGEFI",
            fournisseur=self.fournisseur_sogefi,
        )

        self.client.force_login(self.caissiere_avena)
        response = self.client.get(reverse("paiements_maintenances"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, depense_avena.reference)
        self.assertNotContains(response, depense_sogefi.reference)
        self.assertContains(response, "Caissiere Avena")
        self.assertNotContains(response, "Comptable SOGEFI")

    def test_comptable_avena_ne_voit_pas_les_approvisionnements_sogefi(self):
        caissiere_sogefi_group, _ = Group.objects.get_or_create(name="caissiere")
        caissiere_sogefi = User.objects.create_user(username="moligo", password="testpass123")
        caissiere_sogefi.groups.add(caissiere_sogefi_group)
        appro_sogefi = ApprovisionnementCaisse.objects.create(
            caissiere=caissiere_sogefi,
            saisi_par=caissiere_sogefi,
            date_approvisionnement="2026-05-26",
            nature_approvisionnement=ApprovisionnementCaisse.NATURE_URGENCE_DG,
            montant=Decimal("5000000"),
        )
        appro_avena = ApprovisionnementCaisse.objects.create(
            caissiere=self.caissiere_avena,
            saisi_par=self.comptable_avena,
            date_approvisionnement="2026-05-26",
            nature_approvisionnement=ApprovisionnementCaisse.NATURE_URGENCE_DG,
            montant=Decimal("2000000"),
        )

        self.client.force_login(self.comptable_avena)
        response = self.client.get(reverse("appro_caisse"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, appro_avena.reference)
        self.assertNotContains(response, appro_sogefi.reference)
        self.assertContains(response, "oumou")
        self.assertNotContains(response, "moligo")

    def test_comptable_avena_sans_caissiere_avena_ne_voit_aucun_approvisionnement(self):
        self.caissiere_avena.delete()
        caissiere_sogefi_group, _ = Group.objects.get_or_create(name="caissiere")
        caissiere_sogefi = User.objects.create_user(username="sogefi-caisse", password="testpass123")
        caissiere_sogefi.groups.add(caissiere_sogefi_group)
        appro_sogefi = ApprovisionnementCaisse.objects.create(
            caissiere=caissiere_sogefi,
            saisi_par=caissiere_sogefi,
            date_approvisionnement="2026-05-26",
            nature_approvisionnement=ApprovisionnementCaisse.NATURE_URGENCE_DG,
            montant=Decimal("5000000"),
        )

        self.client.force_login(self.comptable_avena)
        response = self.client.get(reverse("appro_caisse"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, appro_sogefi.reference)
        self.assertEqual(list(response.context["approvisionnements"]), [])


class CaisseDgMouvementsTests(TestCase):
    def setUp(self):
        self.caissiere_group, _ = Group.objects.get_or_create(name="caissiere")
        self.caissiere_soni_group, _ = Group.objects.get_or_create(name="caissiere_soni")
        self.caissiere_avena_group, _ = Group.objects.get_or_create(name="caissiere_avena")
        self.caissiere = User.objects.create_user(username="aminata", password="testpass123")
        self.caissiere.groups.add(self.caissiere_group)
        self.caissiere_soni = User.objects.create_user(username="mariama", password="testpass123")
        self.caissiere_soni.groups.add(self.caissiere_soni_group)
        self.caissiere_avena = User.objects.create_user(username="hadja", password="testpass123")
        self.caissiere_avena.groups.add(self.caissiere_avena_group)

    def _post_mouvement(self, nature, montant):
        return self.client.post(
            reverse("appro_caisse"),
            {
                "caissiere": self.caissiere.id,
                "date_approvisionnement": "2026-05-25",
                "mode_approvisionnement": ApprovisionnementCaisse.MODE_ESPECE,
                "nature_approvisionnement": nature,
                "montant": str(montant),
                "observation": "Test caisse",
            },
        )

    def test_caissiere_peut_saisir_emprunt_remboursement_et_retour_caisse(self):
        self.client.force_login(self.caissiere)

        response = self._post_mouvement(ApprovisionnementCaisse.NATURE_URGENCE_DG, 1000)
        self.assertEqual(response.status_code, 302)
        response = self._post_mouvement(ApprovisionnementCaisse.NATURE_RETOUR_CAISSE, 200)
        self.assertEqual(response.status_code, 302)
        response = self._post_mouvement(ApprovisionnementCaisse.NATURE_REMBOURSEMENT_DG_ESPECE, 300)
        self.assertEqual(response.status_code, 302)

        caisse_metrics = _get_caisse_metrics(self.caissiere)
        dg_metrics = _get_dg_metrics(caissiere=self.caissiere)

        self.assertEqual(caisse_metrics["solde"], Decimal("900"))
        self.assertEqual(caisse_metrics["total_retours_caisse"], Decimal("200"))
        self.assertEqual(caisse_metrics["total_remboursements_dg_caisse"], Decimal("300"))
        self.assertEqual(dg_metrics["solde_dg"], Decimal("700"))

    def test_caissiere_accede_au_compte_dg(self):
        self.client.force_login(self.caissiere)
        response = self.client.get(reverse("situation_dg"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compte DG")

    def test_caissiere_peut_exporter_rapport_et_situation_caisse(self):
        self.client.force_login(self.caissiere)

        situation_response = self.client.get(reverse("export_situation_caisse_xls"))
        rapport_response = self.client.get(reverse("export_rapport_caisse_xls"))

        self.assertEqual(situation_response.status_code, 200)
        self.assertEqual(rapport_response.status_code, 200)
        self.assertEqual(
            situation_response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(
            rapport_response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_exports_caisse_disponibles_pour_soni_et_avena(self):
        for user in [self.caissiere_soni, self.caissiere_avena]:
            with self.subTest(user=user.username):
                self.client.force_login(user)

                situation_page = self.client.get(reverse("situation_caisse"))
                rapport_page = self.client.get(reverse("rapport_maintenances"))
                situation_export = self.client.get(reverse("export_situation_caisse_xls"))
                rapport_export = self.client.get(reverse("export_rapport_caisse_xls"))

                self.assertEqual(situation_page.status_code, 200)
                self.assertContains(situation_page, "Exporter Excel")
                self.assertEqual(rapport_page.status_code, 200)
                self.assertContains(rapport_page, "Exporter Excel")
                self.assertEqual(situation_export.status_code, 200)
                self.assertEqual(rapport_export.status_code, 200)
                self.assertEqual(
                    situation_export["Content-Type"],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                self.assertEqual(
                    rapport_export["Content-Type"],
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )


class PanneManagementTests(TestCase):
    def setUp(self):
        self.logistique_group, _ = Group.objects.get_or_create(name="logistique")
        self.user = User.objects.create_user(username="log_pannes", password="testpass123")
        self.user.groups.add(self.logistique_group)
        self.type_maintenance = TypeMaintenance.objects.create(libelle="Freinage")
        self.panne = PanneCatalogue.objects.create(type_maintenance=self.type_maintenance, libelle="Plaquettes")
        self.fournisseurs = [
            Fournisseur.objects.create(
                nom_fournisseur=f"Fournisseur {index}",
                entreprise=f"Garage {index}",
                portefeuille=Fournisseur.PORTEFEUILLE_LOGISTIQUE,
            )
            for index in range(1, 4)
        ]
        for index, fournisseur in enumerate(self.fournisseurs, start=1):
            PanneFournisseurPrix.objects.create(
                panne=self.panne,
                fournisseur=fournisseur,
                montant=Decimal(index * 100000),
                date_reference="2026-06-01",
            )

    def test_page_gestion_pannes_affiche_trois_colonnes_fournisseurs(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("gerer_pannes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gerer les pannes")
        self.assertContains(response, "Fournisseur 1")
        self.assertContains(response, "Fournisseur 2")
        self.assertContains(response, "Fournisseur 3")
        self.assertContains(response, "Plaquettes")
        self.assertContains(response, "100.000 GNF")

    def test_ajouter_prix_panne_memorise_le_fournisseur(self):
        nouveau = Fournisseur.objects.create(
            nom_fournisseur="Nouveau",
            entreprise="Pieces Auto",
            portefeuille=Fournisseur.PORTEFEUILLE_LOGISTIQUE,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("ajouter_prix_panne", args=[self.panne.id]),
            {
                "fournisseur": str(nouveau.id),
                "montant": "450000",
                "date_reference": "2026-06-02",
                "observation": "Controle prix",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(PanneFournisseurPrix.objects.filter(panne=self.panne, fournisseur=nouveau, montant=Decimal("450000")).exists())
