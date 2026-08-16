"""Tests unitaires pour le lot 1 — ingestion DVF."""

import pandas as pd
import pytest

from src.common.config import PRIX_M2_MAX, PRIX_M2_MIN
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
        assert stats["mutations_retenues"] == 1

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
        [(0, 1), (1, 1), (105_456, 462_796), (1, 3), (999, 1000)],
    )
    def test_toujours_dans_les_bornes(self, retenues: int, brutes: int) -> None:
        assert 0 <= retention_pct(retenues, brutes) <= 100

    def test_valeur_reelle_du_69(self) -> None:
        """Cas réel : 462 796 lignes brutes → 105 456 mutations retenues."""
        assert retention_pct(105_456, 462_796) == 22.8

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
        assert stats["mutations_retenues"] == 2

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

        total = sum(stats[k] for k in EXCLUSION_KEYS) + stats["lignes_regroupees"]
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
        assert stats["mutations_retenues"] == 1

    def test_assertion_declenchee_sur_compteur_faux(self) -> None:
        """Un filtre non instrumenté doit faire échouer le bilan."""
        stats = dict.fromkeys(EXCLUSION_KEYS, 0)
        stats.update({
            "lignes_brutes": 10, "exclues_non_vente": 1, "exclues_type_local": 1,
            "exclues_multi_lots": 1, "lignes_regroupees": 5,
            "mutations_formees": 5, "exclues_prix_m2_aberrant": 0,
            "mutations_retenues": 5,
        })
        with pytest.raises(RuntimeError, match="pas instrumenté"):
            check_balance(stats)


class TestPrixM2Aberrant:
    def test_vente_symbolique_exclue(self) -> None:
        """Une cession à 1 € passe mask_prix (valeur > 0) mais doit être écartée."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", valeur_fonciere="1"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P2", valeur_fonciere="250000"),
        ]
        result, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["exclues_prix_m2_aberrant"] == 1
        assert stats["exclues_prix_manquant"] == 0, "1 € n'est pas un prix manquant"
        assert stats["mutations_retenues"] == 1
        assert "M2" in result["id_mutation"].values

    def test_prix_au_m2_excessif_exclu(self) -> None:
        rows = [
            _make_dvf_row(
                id_mutation="M1", id_parcelle="P1",
                valeur_fonciere="50000000", surface_reelle_bati="20",
            ),
            _make_dvf_row(id_mutation="M2", id_parcelle="P2"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))
        assert stats["exclues_prix_m2_aberrant"] == 1

    @pytest.mark.parametrize("valeur", ["6500", "3200000"])
    def test_bornes_incluses(self, valeur: str) -> None:
        """80 m² : 6 500 € → 81 €/m² exclu ; 3 200 000 € → 40 000 €/m² conservé."""
        rows = [_make_dvf_row(valeur_fonciere=valeur, surface_reelle_bati="80")]
        _, stats = filter_dvf(pd.DataFrame(rows))
        prix_m2 = float(valeur) / 80
        attendu = 0 if PRIX_M2_MIN <= prix_m2 <= PRIX_M2_MAX else 1
        assert stats["exclues_prix_m2_aberrant"] == attendu

    def test_bilan_reste_equilibre(self) -> None:
        """Le nouveau filtre doit rester instrumenté."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", valeur_fonciere="1"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P2", nature_mutation="Echange"),
            _make_dvf_row(id_mutation="M3", id_parcelle="P3"),
            _make_dvf_row(id_mutation="M4", id_parcelle="P4", surface_reelle_bati="5"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))
        total = sum(stats[k] for k in EXCLUSION_KEYS) + stats["lignes_regroupees"]
        assert total == stats["lignes_brutes"] == len(rows)

    def test_bornes_partagees_avec_le_controle_qualite(self) -> None:
        """Filtre d'ingestion et contrôle qualité doivent lire la même source :
        les faire diverger rouvrirait la porte à un build qui échoue."""
        from src.transform import quality_checks

        assert quality_checks.PRIX_M2_MIN is PRIX_M2_MIN
        assert quality_checks.PRIX_M2_MAX is PRIX_M2_MAX


class TestRegroupementParMutation:
    def test_une_mutation_donne_une_ligne(self) -> None:
        """DVF émet une ligne par lot, toutes porteuses de la valeur totale :
        les compter séparément gonflait le nombre de ventes."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="60"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="150"),
        ]
        result, stats = filter_dvf(pd.DataFrame(rows))

        assert len(result) == 1
        assert stats["mutations_retenues"] == 1
        assert stats["lignes_regroupees"] == 2

    def test_surface_sommee_valeur_conservee(self) -> None:
        """Le prix au m² doit se calculer sur la surface totale vendue."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="60"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="150"),
        ]
        result, _ = filter_dvf(pd.DataFrame(rows))
        ligne = result.iloc[0]

        assert ligne["surface_reelle_bati"] == 210
        assert ligne["valeur_fonciere"] == 900000
        assert ligne["nb_lots"] == 2

    def test_doublons_stricts_supprimes(self) -> None:
        """DVF republie certaines lignes à l'identique."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", surface_reelle_bati="80"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", surface_reelle_bati="80"),
        ]
        result, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["doublons_stricts"] == 1
        assert result.iloc[0]["surface_reelle_bati"] == 80, "surface dédoublée"

    def test_mutation_mixte_ecartee(self) -> None:
        """Maison et appartement dans une même mutation : la valeur foncière
        n'est attribuable ni à l'un ni à l'autre."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", type_local="Maison"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P1", type_local="Appartement"),
            _make_dvf_row(id_mutation="M2", id_parcelle="P2"),
        ]
        result, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["exclues_type_mixte"] == 2
        assert stats["mutations_retenues"] == 1
        assert result.iloc[0]["id_mutation"] == "M2"

    def test_prix_m2_evalue_apres_regroupement(self) -> None:
        """900 000 € pour 60 m² dépasse la borne haute, mais la mutation porte
        210 m² au total : elle doit être conservée."""
        rows = [
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="60"),
            _make_dvf_row(id_mutation="M1", id_parcelle="P1",
                          valeur_fonciere="900000", surface_reelle_bati="150"),
        ]
        _, stats = filter_dvf(pd.DataFrame(rows))

        assert stats["exclues_prix_m2_aberrant"] == 0
        assert stats["mutations_retenues"] == 1

    def test_bilan_mutations_verifie(self) -> None:
        """Un décompte de mutations incohérent doit être détecté."""
        stats = dict.fromkeys(EXCLUSION_KEYS, 0)
        stats.update({
            "lignes_brutes": 5, "lignes_regroupees": 5,
            "mutations_formees": 5, "exclues_prix_m2_aberrant": 1,
            "mutations_retenues": 5,
        })
        with pytest.raises(RuntimeError, match="mutations incohérent"):
            check_balance(stats)
