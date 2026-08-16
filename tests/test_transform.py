"""Tests unitaires pour le lot 5 — transformation et agrégats."""

from pathlib import Path

import pandas as pd
import pytest

from src.transform import build_aggregates
from src.transform.build_aggregates import REQUIRED_INPUTS, check_inputs
from src.transform.export_web import effet_dpe_departemental


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
    (root / "communes_historiques.parquet").touch()
    (root / "dvf_dpe.parquet").touch()


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
        assert f"1 sur {len(REQUIRED_INPUTS)}" in message

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
        assert f"{len(REQUIRED_INPUTS)} sur {len(REQUIRED_INPUTS)}" in message
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
            assert motif
            assert commande.startswith("python -m src.")

    def test_rapprochement_est_une_entree_requise(self) -> None:
        """Sans lui, agg_voie_immo et agg_commune_croisement restent vides."""
        assert "dvf_dpe.parquet" in [motif for motif, _ in REQUIRED_INPUTS]


class TestRattachementRequis:
    def test_table_de_rattachement_est_une_entree_requise(self) -> None:
        """Sans elle, les codes de communes fusionnées restent orphelins et le
        contrôle « toute commune a un département » échoue."""
        motifs = [motif for motif, _ in REQUIRED_INPUTS]
        assert "communes_historiques.parquet" in motifs

    def test_commande_de_production_citee(self) -> None:
        commandes = dict(REQUIRED_INPUTS)
        assert "communes_historiques" in commandes["communes_historiques.parquet"]


class TestEffetDpeDepartemental:
    """L'écart de prix par étiquette doit se mesurer à commune constante.

    Agrégé brut sur un département, il fait ressortir les logements classés G
    au-dessus des D : la localisation écrase l'effet énergétique, les G étant
    massivement des immeubles anciens d'hypercentre.
    """

    @staticmethod
    def _croisement(lignes: list[tuple]) -> pd.DataFrame:
        return pd.DataFrame(
            lignes, columns=["code_insee", "classe_dpe", "nb_observations", "prix_m2_median"],
        )

    def test_ecart_relatif_a_la_classe_d(self) -> None:
        d = self._croisement([
            ("69123", "C", 100, 5500), ("69123", "D", 100, 5000),
        ])
        effet = effet_dpe_departemental(d)

        assert effet["D"][0] == 0.0
        assert effet["C"][0] == pytest.approx(10.0)

    def test_localisation_neutralisee(self) -> None:
        """Commune chère où le G domine, commune bon marché où le C domine :
        agrégé brut, le G ressortirait gagnant. À commune constante, non."""
        d = self._croisement([
            # commune chère : le G y vaut moins que le D
            ("69123", "D", 100, 6000), ("69123", "G", 900, 5400),
            # commune bon marché : le C y vaut plus que le D
            ("69300", "D", 100, 2000), ("69300", "C", 100, 2400),
        ])
        effet = effet_dpe_departemental(d)

        assert effet["G"][0] == pytest.approx(-10.0), "le G doit rester sous le D"
        assert effet["C"][0] == pytest.approx(20.0)

    def test_ponderation_par_les_effectifs(self) -> None:
        """Une commune à 1 000 ventes doit peser plus qu'une commune à 10."""
        d = self._croisement([
            ("69123", "D", 1000, 5000), ("69123", "C", 1000, 5500),
            ("69300", "D", 10, 2000), ("69300", "C", 10, 4000),
        ])
        effet = effet_dpe_departemental(d)

        assert effet["C"][0] < 30, "la petite commune ne doit pas dominer"
        assert effet["C"][1] == 1010

    def test_commune_sans_reference_ignoree(self) -> None:
        """Sans classe D, aucune comparaison intra-commune n'est possible."""
        d = self._croisement([("69123", "C", 100, 5000), ("69123", "E", 100, 4000)])
        assert effet_dpe_departemental(d) == {}

    def test_effectifs_faibles_ecartes(self) -> None:
        d = self._croisement([("69123", "D", 2, 5000), ("69123", "C", 2, 9000)])
        assert effet_dpe_departemental(d) == {}

    def test_jeu_vide(self) -> None:
        assert effet_dpe_departemental(self._croisement([])) == {}
