"""Tests unitaires pour le lot 1 — ingestion DVF."""

import pandas as pd
import pytest

from src.ingest.dvf import (
    EXCLUSION_KEYS,
    RETENTION_MAX_PCT,
    RETENTION_MIN_PCT,
    check_balance,
    filter_dvf,
    retention_pct,
    validate_schema,
)


def _make_dvf_row(**overrides) -> dict:
    """Construit une ligne DVF valide avec possibilité de surcharger des champs."""
    base = {
        "id_mutation": "2024-1",
        "date_mutation": "2024-01-15",
        "nature_mutation": "Vente",
        "valeur_fonciere": "250000",
        "adresse_numero": "12",
        "adresse_nom_voie": "RUE DE LA PAIX",
        "adresse_code_voie": "1234",
        "code_postal": "69001",
        "code_commune": "69381",
        "nom_commune": "Lyon 1er Arrondissement",
        "code_departement": "69",
        "id_parcelle": "69381000AB0001",
        "type_local": "Appartement",
        "surface_reelle_bati": "65",
        "nombre_pieces_principales": "3",
        "surface_terrain": "0",
        "longitude": "4.83",
        "latitude": "45.77",
    }
    base.update(overrides)
    return base


class TestValidateSchema:
    def test_valid_schema(self) -> None:
        df = pd.DataFrame([_make_dvf_row()])
        validate_schema(df)

    def test_missing_column_raises(self) -> None:
        row = _make_dvf_row()
        del row["valeur_fonciere"]
        df = pd.DataFrame([row])
        with pytest.raises(RuntimeError, match="valeur_fonciere"):
            validate_schema(df)


class TestFilterDvf:
    def test_retains_valid_sale(self) -> None:
        df = pd.DataFrame([_make_dvf_row()])
        result, stats = filter_dvf(df)
        assert len(result) == 1
        assert stats["lignes_retenues"] == 1

    def test_excludes_non_vente(self) -> None:
        df = pd.DataFrame([_make_dvf_row(nature_mutation="Échange")])
        result, stats = filter_dvf(df)
        assert len(result) == 0

    def test_excludes_wrong_type_local(self) -> None:
        df = pd.DataFrame([_make_dvf_row(type_local="Dépendance")])
        result, stats = filter_dvf(df)
        assert len(result) == 0

    def test_excludes_no_price(self) -> None:
        df = pd.DataFrame([_make_dvf_row(valeur_fonciere="")])
        result, stats = filter_dvf(df)
        assert len(result) == 0

    def test_excludes_small_surface(self) -> None:
        df = pd.DataFrame([_make_dvf_row(surface_reelle_bati="8")])
        result, stats = filter_dvf(df)
        assert len(result) == 0

    def test_excludes_multi_lots(self) -> None:
        """Vérifie que les mutations multi-lots (plusieurs parcelles) sont exclues."""
        rows = [
            _make_dvf_row(id_mutation="mut-multi", id_parcelle="PARCELLE_A"),
            _make_dvf_row(id_mutation="mut-multi", id_parcelle="PARCELLE_B"),
            _make_dvf_row(id_mutation="mut-simple", id_parcelle="PARCELLE_C"),
        ]
        df = pd.DataFrame(rows)
        result, stats = filter_dvf(df)
        assert "mut-multi" not in result["id_mutation"].values
        assert "mut-simple" in result["id_mutation"].values
        # 1 mutation multi-lots, mais 2 lignes retirées : le compteur est en lignes
        assert stats["exclues_multi_lots"] == 2
        assert stats["mutations_multi_lots"] == 1

    def test_comma_decimal_price(self) -> None:
        """DVF utilise la virgule comme séparateur décimal."""
        df = pd.DataFrame([_make_dvf_row(valeur_fonciere="250000,50")])
        result, stats = filter_dvf(df)
        assert len(result) == 1
        assert result.iloc[0]["valeur_fonciere"] == 250000.50


class TestTauxRetention:
    def test_borne_a_100(self) -> None:
        assert retention_pct(10, 10) == 100.0

    def test_borne_a_0(self) -> None:
        assert retention_pct(0, 10) == 0.0

    def test_volume_brut_nul(self) -> None:
        """Aucune division par zéro sur un fichier vide."""
        assert retention_pct(0, 0) == 0.0

    @pytest.mark.parametrize(
        ("retenues", "brutes"),
        [(0, 1), (1, 1), (144_074, 462_796), (1, 3), (999, 1000)],
    )
    def test_toujours_dans_les_bornes(self, retenues: int, brutes: int) -> None:
        assert 0 <= retention_pct(retenues, brutes) <= 100

    def test_valeur_reelle_du_69(self) -> None:
        """Cas réel : 462 796 brutes → 144 074 retenues."""
        assert retention_pct(144_074, 462_796) == 31.1

    def test_plage_attendue_coherente(self) -> None:
        assert 0 < RETENTION_MIN_PCT < RETENTION_MAX_PCT <= 100


class TestBilanDesFiltres:
    def test_multi_lots_compte_des_lignes_pas_des_mutations(self) -> None:
        """Une mutation multi-lots porte plusieurs lignes : compter les mutations
        sous-estime l'exclusion et déséquilibre le bilan."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P2"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P3"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P4"),
            _make_dvf_row(id_mutation="M3", id_parcelle="P5"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["exclues_multi_lots"] == 3
        assert stats["mutations_multi_lots"] == 1
        assert stats["lignes_retenues"] == 2

    def test_bilan_equilibre(self) -> None:
        """exclusions + retenues doit reconstituer le volume brut."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P2"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P3"),
            _make_dvf_row(id_mutation="M3", id_parcelle="P4", nature_mutation="Echange"),
            _make_dvf_row(id_mutation="M4", id_parcelle="P5", type_local="Dépendance"),
            _make_dvf_row(id_mutation="M5", id_parcelle="P6", valeur_fonciere="0"),
            _make_dvf_row(id_mutation="M6", id_parcelle="P7", surface_reelle_bati="5"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))

        total = sum(stats[k] for k in EXCLUSION_KEYS) + stats["lignes_retenues"]
        assert total == stats["lignes_brutes"] == len(rows)

    def test_chaque_filtre_a_son_compteur(self) -> None:
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P2", nature_mutation="Echange"),
            _make_dvf_row(id_mutation="M3", id_parcelle="P3", type_local="Local industriel"),
            _make_dvf_row(id_mutation="M4", id_parcelle="P4", valeur_fonciere="0"),
            _make_dvf_row(id_mutation="M5", id_parcelle="P5", surface_reelle_bati="5"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["exclues_non_vente"] == 1
        assert stats["exclues_type_local"] == 1
        assert stats["exclues_prix_manquant"] == 1
        assert stats["exclues_surface_faible"] == 1
        assert stats["lignes_retenues"] == 1

    def test_assertion_declenchee_sur_compteur_faux(self) -> None:
        """Un filtre non instrumenté doit faire échouer le bilan."""
        stats = {
            "lignes_brutes": 10, "exclues_non_vente": 1, "exclues_type_local": 1,
            "exclues_prix_manquant": 0, "exclues_surface_faible": 0,
            "exclues_multi_lots": 1, "lignes_retenues": 5,
        }
        with pytest.raises(RuntimeError, match="pas instrumenté"):
            check_balance(stats)
