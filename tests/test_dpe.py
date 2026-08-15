"""Tests unitaires pour le lot 2 — ingestion DPE."""

import pytest

from src.ingest.dpe import COLUMN_RENAME, SELECT_COLUMNS, build_query_params


class TestDpeConfig:
    def test_select_columns_all_have_rename(self) -> None:
        """Chaque colonne sélectionnée doit avoir une correspondance de renommage."""
        for col in SELECT_COLUMNS:
            assert col in COLUMN_RENAME, (
                f"Colonne {col} sans correspondance"
            )

    def test_renamed_columns_are_valid_identifiers(self) -> None:
        """Les noms renommés doivent être des identifiants Python/SQL valides."""
        for original, renamed in COLUMN_RENAME.items():
            assert renamed.isidentifier(), f"{original} → {renamed} n'est pas un identifiant valide"
            assert renamed == renamed.lower(), f"{original} → {renamed} n'est pas en minuscules"


class TestBuildQueryParams:
    def test_parentheses_des_noms_de_champs_sont_echappees(self) -> None:
        """Sans échappement, `(` et `)` sont des opérateurs de groupement
        query_string : le filtre serait ignoré et toute la France téléchargée."""
        qs = build_query_params(code_postal="69001")["qs"]
        assert qs == r"Code_postal_\(BAN\):69001"

    def test_filtre_departement_utilise_le_code_insee(self) -> None:
        """Le préfixe du code INSEE désigne le département de façon fiable,
        contrairement au code postal."""
        qs = build_query_params(departement="69")["qs"]
        assert qs == r"Code_INSEE_\(BAN\):69*"

    def test_joker_du_filtre_departement_reste_actif(self) -> None:
        """Le `*` terminal ne doit pas être échappé, sinon il devient littéral."""
        qs = build_query_params(departement="69")["qs"]
        assert qs.endswith("69*")
        assert not qs.endswith(r"\*")

    def test_departement_corse(self) -> None:
        assert build_query_params(departement="2A")["qs"] == r"Code_INSEE_\(BAN\):2A*"

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
