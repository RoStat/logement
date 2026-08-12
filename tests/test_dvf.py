"""Tests unitaires pour le lot 1 — ingestion DVF."""

import pandas as pd
import pytest

from src.ingest.dvf import filter_dvf, validate_schema


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
        assert stats["exclues_multi_lots"] == 1

    def test_comma_decimal_price(self) -> None:
        """DVF utilise la virgule comme séparateur décimal."""
        df = pd.DataFrame([_make_dvf_row(valeur_fonciere="250000,50")])
        result, stats = filter_dvf(df)
        assert len(result) == 1
        assert result.iloc[0]["valeur_fonciere"] == 250000.50
