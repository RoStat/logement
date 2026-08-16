"""Tests unitaires pour le lot 10 — loyers d'annonce."""

import pandas as pd
import pytest

from src.ingest.loyers import CATEGORIES, COLONNES_ATTENDUES, nombre


class TestConversionNumerique:
    """Le fichier source est lu en chaînes pour préserver les codes INSEE à zéro
    initial ; `decimal=","` reste alors sans effet et les virgules subsistent."""

    def test_virgule_decimale_convertie(self) -> None:
        assert nombre(pd.Series(["9,75769624568385"])).iloc[0] == pytest.approx(9.7577)

    def test_point_decimal_accepte(self) -> None:
        assert nombre(pd.Series(["12.5"])).iloc[0] == 12.5

    def test_entier(self) -> None:
        assert nombre(pd.Series(["484"])).iloc[0] == 484

    def test_valeur_illisible_devient_nulle(self) -> None:
        assert pd.isna(nombre(pd.Series(["n/a"])).iloc[0])

    def test_serie_vide(self) -> None:
        assert nombre(pd.Series([], dtype=str)).empty

    def test_les_loyers_ne_sont_pas_tous_nuls(self) -> None:
        """Sans conversion, toute la colonne devenait NaN et les médianes
        s'affichaient à « nan » sans que rien n'échoue."""
        serie = nombre(pd.Series(["12,63", "14,59", "11,11"]))
        assert serie.notna().all()
        assert serie.median() == pytest.approx(12.63)


class TestSchema:
    def test_colonnes_attendues_couvrent_l_essentiel(self) -> None:
        assert {"INSEE_C", "loypredm2", "TYPPRED"} <= COLONNES_ATTENDUES

    def test_categories_mappees_vers_des_identifiants_simples(self) -> None:
        for cible in set(CATEGORIES.values()):
            assert cible.replace("_", "").isalnum()
            assert cible == cible.lower()

    def test_appartement_et_maison_couverts(self) -> None:
        cibles = set(CATEGORIES.values())
        assert "appartement" in cibles
        assert "maison" in cibles

    def test_intitules_reconnus_en_minuscules(self) -> None:
        """La correspondance se fait sur l'intitulé normalisé : un producteur
        qui change la casse ne doit pas faire disparaître une catégorie."""
        for intitule in CATEGORIES:
            assert intitule == intitule.lower()
