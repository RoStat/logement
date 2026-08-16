"""Tests unitaires pour le lot 3bis — rattachement des communes fusionnées."""

import pandas as pd

from src.ingest.communes_historiques import build_rattachements


def _cog(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["TYPECOM", "COM", "DEP", "LIBELLE", "COMPARENT"])


class TestBuildRattachements:
    def test_commune_deleguee_rattachee(self) -> None:
        """Cas réel : Pierre-Bénite fusionnée dans Oullins-Pierre-Bénite."""
        cog = _cog([
            {"TYPECOM": "COM", "COM": "69149", "DEP": "69",
             "LIBELLE": "Oullins-Pierre-Bénite", "COMPARENT": None},
            {"TYPECOM": "COMD", "COM": "69152", "DEP": None,
             "LIBELLE": "Pierre-Bénite", "COMPARENT": "69149"},
        ])
        result = build_rattachements(cog)

        assert len(result) == 1
        ligne = result.iloc[0]
        assert ligne["code_insee_historique"] == "69152"
        assert ligne["code_insee"] == "69149"
        assert ligne["code_departement"] == "69"

    def test_commune_associee_rattachee(self) -> None:
        """Les COMA doivent être traitées comme les COMD."""
        cog = _cog([
            {"TYPECOM": "COM", "COM": "01001", "DEP": "01",
             "LIBELLE": "Nouvelle", "COMPARENT": None},
            {"TYPECOM": "COMA", "COM": "01002", "DEP": None,
             "LIBELLE": "Associée", "COMPARENT": "01001"},
        ])
        assert len(build_rattachements(cog)) == 1

    def test_fusion_en_chaine_resolue(self) -> None:
        """Une commune déléguée peut pointer vers une commune elle-même absorbée
        lors d'une fusion ultérieure : la chaîne doit être remontée."""
        cog = _cog([
            {"TYPECOM": "COM", "COM": "69001", "DEP": "69",
             "LIBELLE": "Finale", "COMPARENT": None},
            {"TYPECOM": "COMD", "COM": "69002", "DEP": None,
             "LIBELLE": "Intermédiaire", "COMPARENT": "69001"},
            {"TYPECOM": "COMD", "COM": "69003", "DEP": None,
             "LIBELLE": "Ancienne", "COMPARENT": "69002"},
        ])
        result = build_rattachements(cog).set_index("code_insee_historique")

        assert result.loc["69003", "code_insee"] == "69001", "chaîne non remontée"
        assert result.loc["69003", "code_departement"] == "69"

    def test_communes_actives_absentes_du_resultat(self) -> None:
        """Seules les communes absorbées figurent dans la table."""
        cog = _cog([
            {"TYPECOM": "COM", "COM": "69123", "DEP": "69",
             "LIBELLE": "Lyon", "COMPARENT": None},
        ])
        assert build_rattachements(cog).empty

    def test_parent_introuvable_ignore(self) -> None:
        """Un rattachement non résoluble est écarté plutôt que propagé."""
        cog = _cog([
            {"TYPECOM": "COMD", "COM": "69999", "DEP": None,
             "LIBELLE": "Orpheline", "COMPARENT": "99999"},
        ])
        assert build_rattachements(cog).empty

    def test_cycle_ne_boucle_pas(self) -> None:
        """Deux communes se désignant mutuellement ne doivent pas figer le script."""
        cog = _cog([
            {"TYPECOM": "COMD", "COM": "A", "DEP": None, "LIBELLE": "A", "COMPARENT": "B"},
            {"TYPECOM": "COMD", "COM": "B", "DEP": None, "LIBELLE": "B", "COMPARENT": "A"},
        ])
        assert build_rattachements(cog).empty

    def test_pas_de_code_historique_en_double(self) -> None:
        cog = _cog([
            {"TYPECOM": "COM", "COM": "69001", "DEP": "69",
             "LIBELLE": "Nouvelle", "COMPARENT": None},
            {"TYPECOM": "COMD", "COM": "69002", "DEP": None,
             "LIBELLE": "A", "COMPARENT": "69001"},
            {"TYPECOM": "COMD", "COM": "69003", "DEP": None,
             "LIBELLE": "B", "COMPARENT": "69001"},
        ])
        result = build_rattachements(cog)
        assert not result["code_insee_historique"].duplicated().any()
        assert len(result) == 2
