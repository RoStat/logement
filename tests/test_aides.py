"""Tests unitaires pour le lot 4 — aides publiques."""

import copy

import pytest

from src.ingest.aides import BENEFICIAIRES, aplatir, charger, valider


@pytest.fixture
def fiche() -> dict:
    return copy.deepcopy(charger())


class TestFicheReelle:
    def test_fiche_conforme(self, fiche: dict) -> None:
        assert valider(fiche) == []

    def test_aucun_montant_affiche(self, fiche: dict) -> None:
        """Le site ne connaît ni les revenus ni la composition du foyer, et les
        barèmes changent par décret : annoncer une somme induirait l'utilisateur
        en erreur sur son budget."""
        assert valider(fiche) == []

    def test_toutes_les_sources_en_https(self, fiche: dict) -> None:
        for d in fiche["dispositifs"]:
            assert d["source"]["url"].startswith("https://")

    def test_le_locataire_est_couvert(self, fiche: dict) -> None:
        """Le site s'adresse d'abord à des locataires : au moins un dispositif
        doit les concerner directement."""
        concernes = {b for d in fiche["dispositifs"] for b in d["pour_qui"]}
        assert "locataire" in concernes

    def test_un_simulateur_officiel_est_cite(self, fiche: dict) -> None:
        """Puisqu'aucun montant n'est donné, l'utilisateur doit savoir où aller."""
        assert fiche["simulateur"]["url"].startswith("https://")
        assert fiche["avertissement"]


class TestValidation:
    def test_montant_en_euros_rejete(self, fiche: dict) -> None:
        fiche["dispositifs"][0]["resume"] = "Une aide pouvant atteindre 10 000 €."
        anomalies = valider(fiche)
        assert any("montant chiffré" in a for a in anomalies)

    def test_montant_en_toutes_lettres_rejete(self, fiche: dict) -> None:
        fiche["dispositifs"][0]["conditions"] = ["Prime de 500 euros par logement"]
        assert any("montant chiffré" in a for a in valider(fiche))

    def test_pourcentage_rejete(self, fiche: dict) -> None:
        """Une prise en charge « à 30 % » est un barème comme un autre."""
        fiche["dispositifs"][0]["resume"] = "Prise en charge à 30 % du devis."
        assert any("montant chiffré" in a for a in valider(fiche))

    def test_taux_zero_accepte(self, fiche: dict) -> None:
        """« Taux zéro » nomme un dispositif, ce n'est pas un barème."""
        fiche["dispositifs"][0]["resume"] = "Un prêt à taux zéro, sans intérêts."
        assert valider(fiche) == []

    def test_duree_en_annees_acceptee(self, fiche: dict) -> None:
        fiche["dispositifs"][0]["conditions"] = ["Engagement sur 6 ans minimum"]
        assert valider(fiche) == []

    def test_beneficiaire_inconnu_rejete(self, fiche: dict) -> None:
        fiche["dispositifs"][0]["pour_qui"] = ["squatteur"]
        assert any("bénéficiaires inconnus" in a for a in valider(fiche))

    def test_identifiant_en_double_rejete(self, fiche: dict) -> None:
        fiche["dispositifs"].append(copy.deepcopy(fiche["dispositifs"][0]))
        assert any("double" in a for a in valider(fiche))

    def test_source_non_https_rejetee(self, fiche: dict) -> None:
        fiche["dispositifs"][0]["source"]["url"] = "http://exemple.fr"
        assert any("https" in a for a in valider(fiche))

    def test_champ_manquant_rejete(self, fiche: dict) -> None:
        del fiche["dispositifs"][0]["conditions"]
        assert any("champs absents" in a for a in valider(fiche))

    def test_avertissement_obligatoire(self, fiche: dict) -> None:
        fiche["avertissement"] = ""
        assert any("avertissement" in a for a in valider(fiche))

    def test_beneficiaires_connus(self) -> None:
        assert {
            "locataire", "proprietaire_occupant", "proprietaire_bailleur",
        } == BENEFICIAIRES


class TestAplatir:
    def test_une_ligne_par_dispositif(self, fiche: dict) -> None:
        df = aplatir(fiche)
        assert len(df) == len(fiche["dispositifs"])

    def test_colonnes_exportees(self, fiche: dict) -> None:
        df = aplatir(fiche)
        assert {"id", "nom", "resume", "pour_qui", "source_url", "millesime"} <= set(df.columns)

    def test_millesime_reporte_sur_chaque_ligne(self, fiche: dict) -> None:
        df = aplatir(fiche)
        assert (df["millesime"] == fiche["millesime"]).all()
