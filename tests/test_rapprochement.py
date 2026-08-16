"""Tests unitaires pour le lot 8 — rapprochement DVF ↔ DPE."""

import pandas as pd
import pytest

from src.transform.rapprochement import (
    DISTANCE_MAX_M,
    ECART_SURFACE_MAX,
    distance_m,
    normaliser_voie,
    rapprocher,
    separer_type_voie,
)


class TestNormaliserVoie:
    @pytest.mark.parametrize(
        ("saisie", "attendu"),
        [
            ("RUE DES COLLONGES", "RUE COLLONGES"),
            ("Rue des Collonges", "RUE COLLONGES"),
            ("AV. JEAN JAURÈS", "AVENUE JEAN JAURES"),
            ("Bd de la Croix-Rousse", "BOULEVARD CROIX ROUSSE"),
            ("RTE DE VIENNE", "ROUTE VIENNE"),
            ("Ch. du Moulin", "CHEMIN MOULIN"),
        ],
    )
    def test_normalisation(self, saisie: str, attendu: str) -> None:
        """DVF écrit en majuscules abrégées, l'ADEME en casse mixte : sans
        normalisation commune, aucune adresse ne s'apparie."""
        assert normaliser_voie(saisie) == attendu

    def test_saint_abrege(self) -> None:
        assert normaliser_voie("RUE ST EXUPERY") == normaliser_voie("Rue Saint Exupéry")

    def test_valeur_absente(self) -> None:
        assert normaliser_voie(None) == ""
        assert normaliser_voie(float("nan")) == ""

    def test_articles_retires(self) -> None:
        """« Rue de la République » et « Rue République » désignent la même voie."""
        assert normaliser_voie("RUE DE LA REPUBLIQUE") == normaliser_voie("Rue République")


class TestSeparerTypeVoie:
    def test_type_reconnu(self) -> None:
        assert separer_type_voie("AVENUE JEAN JAURES") == ("AVENUE", "JEAN JAURES")

    def test_sans_type(self) -> None:
        assert separer_type_voie("GRANDE GRIGNARD") == ("", "GRANDE GRIGNARD")

    def test_permet_de_rapprocher_rue_et_cours(self) -> None:
        """À Villeurbanne, DVF peut dire « rue » là où la BAN dit « cours »."""
        _, a = separer_type_voie(normaliser_voie("RUE DE LA REPUBLIQUE"))
        _, b = separer_type_voie(normaliser_voie("Cours de la République"))
        assert a == b == "REPUBLIQUE"


class TestDistance:
    def test_point_identique(self) -> None:
        assert distance_m(45.75, 4.85, 45.75, 4.85) == pytest.approx(0, abs=1)

    def test_cent_metres_nord(self) -> None:
        d = distance_m(45.75, 4.85, 45.75 + 100 / 111_320, 4.85)
        assert d == pytest.approx(100, rel=0.02)

    def test_longitude_corrigee_par_la_latitude(self) -> None:
        """Un degré de longitude vaut moins qu'un degré de latitude à nos
        latitudes : sans correction, la distance est surestimée de 30 %."""
        d = distance_m(45.75, 4.85, 45.75, 4.86)
        assert d == pytest.approx(0.01 * 111_320 * 0.698, rel=0.03)


def _vente(**kw) -> dict:
    base = {
        "id_mutation": "M1", "code_commune": "69266", "code_departement": "69",
        "adresse_numero": "12", "adresse_nom_voie": "RUE DE LA REPUBLIQUE",
        "adresse_code_voie": "1234", "type_local": "Appartement",
        "date_mutation": "2025-05-01", "valeur_fonciere": 250000.0,
        "surface_reelle_bati": 65.0, "latitude": 45.7700, "longitude": 4.8800,
    }
    base.update(kw)
    return base


def _diagnostic(**kw) -> dict:
    base = {
        "numero_dpe": "D1", "code_insee": "69266", "code_departement": "69",
        "numero_voie": "12", "nom_rue": "Rue de la République",
        "classe_dpe": "D", "classe_ges": "C", "conso_energie": 210.0,
        "surface_habitable": 65.0, "score_ban": 0.8,
        "geopoint": "45.7700,4.8800",
    }
    base.update(kw)
    return base


class TestRapprocher:
    def test_appariement_nominal(self) -> None:
        res, stats = rapprocher(pd.DataFrame([_vente()]), pd.DataFrame([_diagnostic()]))

        assert stats["apparies_total"] == 1
        assert res.iloc[0]["classe_dpe"] == "D"
        assert res.iloc[0]["methode"] == "stricte"

    def test_distance_excessive_rejetee(self) -> None:
        """Deux adresses homonymes éloignées ne sont pas le même bâtiment."""
        loin = _diagnostic(geopoint=f"{45.7700 + 3 * DISTANCE_MAX_M / 111_320},4.8800")
        _, stats = rapprocher(pd.DataFrame([_vente()]), pd.DataFrame([loin]))

        assert stats["apparies_total"] == 0

    def test_surface_incoherente_rejetee(self) -> None:
        """À une même adresse, un T2 vendu n'est pas le T5 diagnostiqué."""
        autre = _diagnostic(surface_habitable=65 * (1 + 2 * ECART_SURFACE_MAX))
        _, stats = rapprocher(pd.DataFrame([_vente()]), pd.DataFrame([autre]))

        assert stats["apparies_total"] == 0

    def test_geocodage_douteux_ecarte(self) -> None:
        """Un diagnostic mal géocodé ne peut pas prouver une localisation."""
        _, stats = rapprocher(
            pd.DataFrame([_vente()]), pd.DataFrame([_diagnostic(score_ban=0.1)]),
        )
        assert stats["dpe_exploitables"] == 0
        assert stats["apparies_total"] == 0

    def test_meilleure_surface_retenue(self) -> None:
        """Un immeuble porte plusieurs diagnostics : celui dont la surface colle
        le mieux au bien vendu doit l'emporter."""
        candidats = pd.DataFrame([
            _diagnostic(numero_dpe="D_loin", surface_habitable=75.0, classe_dpe="F"),
            _diagnostic(numero_dpe="D_juste", surface_habitable=65.0, classe_dpe="B"),
        ])
        res, _ = rapprocher(pd.DataFrame([_vente()]), candidats)

        assert len(res) == 1
        assert res.iloc[0]["numero_dpe"] == "D_juste"
        assert res.iloc[0]["classe_dpe"] == "B"

    def test_cle_souple_sur_type_de_voie_divergent(self) -> None:
        """« rue » côté DVF, « cours » côté BAN : la clé souple rattrape, et la
        distance confirme."""
        res, stats = rapprocher(
            pd.DataFrame([_vente()]),
            pd.DataFrame([_diagnostic(nom_rue="Cours de la République")]),
        )
        assert stats["apparies_cle_stricte"] == 0
        assert stats["apparies_cle_souple"] == 1
        assert res.iloc[0]["methode"] == "souple"

    def test_une_vente_ne_produit_qu_une_ligne(self) -> None:
        """Sans dédoublonnage, une vente en immeuble se démultiplierait et
        fausserait tous les agrégats."""
        candidats = pd.DataFrame([
            _diagnostic(numero_dpe=f"D{i}", surface_habitable=65.0) for i in range(5)
        ])
        res, _ = rapprocher(pd.DataFrame([_vente()]), candidats)

        assert len(res) == 1

    def test_vente_sans_coordonnees_ecartee(self) -> None:
        _, stats = rapprocher(
            pd.DataFrame([_vente(latitude=None)]), pd.DataFrame([_diagnostic()]),
        )
        assert stats["ventes_geolocalisees"] == 0
