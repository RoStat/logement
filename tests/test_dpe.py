"""Tests unitaires pour le lot 2 — ingestion DPE."""

from src.ingest.dpe import COLUMN_RENAME, SELECT_COLUMNS


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
