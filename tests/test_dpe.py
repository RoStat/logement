"""Tests unitaires pour le lot 2 — ingestion DPE."""

import pytest

from src.ingest.dpe import (
    COLUMN_RENAME,
    NUMERIC_COLUMNS,
    SCHEMA_URL,
    SELECT_COLUMNS,
    build_query_params,
)


class TestDpeConfig:
    def test_select_columns_all_have_rename(self) -> None:
        """Chaque colonne sélectionnée doit avoir une correspondance de renommage."""
        for col in SELECT_COLUMNS:
            assert col in COLUMN_RENAME, f"Colonne {col} sans correspondance"

    def test_renamed_columns_are_valid_identifiers(self) -> None:
        """Les noms renommés doivent être des identifiants Python/SQL valides."""
        for original, renamed in COLUMN_RENAME.items():
            assert renamed.isidentifier(), f"{original} → {renamed} n'est pas un identifiant valide"
            assert renamed == renamed.lower(), f"{original} → {renamed} n'est pas en minuscules"

    def test_renommage_sans_collision(self) -> None:
        """Deux champs sources ne doivent pas aboutir au même nom interne."""
        cibles = list(COLUMN_RENAME.values())
        assert len(cibles) == len(set(cibles))

    def test_colonnes_numeriques_sont_des_noms_internes(self) -> None:
        """La coercition s'applique après renommage."""
        for col in NUMERIC_COLUMNS:
            assert col in COLUMN_RENAME.values()

    def test_noms_de_champs_sans_caracteres_speciaux(self) -> None:
        """Le jeu `dpe03existant` expose des champs en minuscules avec tirets bas.
        Un champ accentué ou parenthésé signale un retour à l'ancien schéma."""
        for col in SELECT_COLUMNS:
            assert col.strip("_").replace("_", "").isalnum(), f"{col} : caractère inattendu"
            assert col == col.lower(), f"{col} n'est pas en minuscules"

    def test_url_du_schema_derivee_de_l_endpoint(self) -> None:
        assert SCHEMA_URL.endswith("/schema")
        assert "dpe03existant" in SCHEMA_URL


class TestBuildQueryParams:
    def test_filtre_code_postal(self) -> None:
        assert build_query_params(code_postal="69001")["qs"] == "code_postal_ban:69001"

    def test_filtre_departement(self) -> None:
        """Le jeu expose le département : aucun préfixe à déduire."""
        assert build_query_params(departement="69")["qs"] == "code_departement_ban:69"

    def test_sans_filtre_aucun_qs(self) -> None:
        """--all ne doit poser aucun filtre."""
        assert "qs" not in build_query_params()

    def test_filtres_mutuellement_exclusifs(self) -> None:
        with pytest.raises(ValueError, match="incompatibles"):
            build_query_params(code_postal="69001", departement="69")

    def test_curseur_transmis(self) -> None:
        assert build_query_params(cursor="abc")["after"] == "abc"

    def test_selection_de_colonnes_toujours_presente(self) -> None:
        for params in (build_query_params(), build_query_params(departement="69")):
            assert params["select"] == ",".join(SELECT_COLUMNS)
