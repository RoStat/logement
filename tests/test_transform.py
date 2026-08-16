"""Tests unitaires pour le lot 5 — transformation et agrégats."""

from pathlib import Path

import pytest

from src.transform import build_aggregates
from src.transform.build_aggregates import REQUIRED_INPUTS, check_inputs


@pytest.fixture
def parquet_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirige PARQUET_DIR vers un répertoire temporaire vide."""
    monkeypatch.setattr(build_aggregates, "PARQUET_DIR", tmp_path)
    return tmp_path


def _create_all(root: Path) -> None:
    (root / "dvf").mkdir(parents=True, exist_ok=True)
    (root / "dpe").mkdir(parents=True, exist_ok=True)
    (root / "dvf" / "dvf_2023.parquet").touch()
    (root / "dpe" / "dpe_dep69.parquet").touch()
    (root / "communes.parquet").touch()


class TestCheckInputs:
    def test_passe_quand_tout_est_present(self, parquet_dir: Path) -> None:
        _create_all(parquet_dir)
        check_inputs()

    def test_signale_le_dpe_manquant(self, parquet_dir: Path) -> None:
        """Cas rencontré en réel : DVF et géo ingérés, DPE absent."""
        _create_all(parquet_dir)
        (parquet_dir / "dpe" / "dpe_dep69.parquet").unlink()

        with pytest.raises(RuntimeError) as exc:
            check_inputs()

        message = str(exc.value)
        assert "dpe" in message
        assert "python -m src.ingest.dpe" in message, "la commande à lancer doit être citée"
        assert "1 sur 3" in message

    def test_signale_les_communes_manquantes(self, parquet_dir: Path) -> None:
        _create_all(parquet_dir)
        (parquet_dir / "communes.parquet").unlink()

        with pytest.raises(RuntimeError, match="communes.parquet"):
            check_inputs()

    def test_liste_tous_les_manquants(self, parquet_dir: Path) -> None:
        """Aucun fichier présent : les trois doivent être signalés d'un coup."""
        with pytest.raises(RuntimeError) as exc:
            check_inputs()

        message = str(exc.value)
        assert "3 sur 3" in message
        for _, commande in REQUIRED_INPUTS:
            assert commande in message

    def test_repertoire_vide_compte_comme_manquant(self, parquet_dir: Path) -> None:
        """Un répertoire dvf/ existant mais vide ne suffit pas."""
        _create_all(parquet_dir)
        (parquet_dir / "dvf" / "dvf_2023.parquet").unlink()

        with pytest.raises(RuntimeError, match="dvf"):
            check_inputs()

    def test_chaque_entree_a_une_commande(self) -> None:
        for motif, commande in REQUIRED_INPUTS:
            assert motif and commande.startswith("python -m src.ingest.")
