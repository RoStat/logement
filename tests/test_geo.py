"""Tests unitaires pour le lot 3 — référentiel géographique."""

import pandas as pd

from src.ingest.geo import deduplicate_slugs, slugify


class TestSlugify:
    def test_saint_etienne(self) -> None:
        assert slugify("Saint-Étienne") == "saint-etienne"

    def test_hay_les_roses(self) -> None:
        assert slugify("L'Haÿ-les-Roses") == "l-hay-les-roses"

    def test_basic(self) -> None:
        assert slugify("Paris") == "paris"

    def test_apostrophe(self) -> None:
        assert slugify("L'Isle-sur-la-Sorgue") == "l-isle-sur-la-sorgue"

    def test_multiple_dashes(self) -> None:
        assert slugify("Aix-en-Provence") == "aix-en-provence"

    def test_cedilla(self) -> None:
        assert slugify("Besançon") == "besancon"

    def test_no_trailing_dash(self) -> None:
        result = slugify("  Toulouse  ")
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestDeduplicateSlugs:
    """Le slug sert d'URL publique : il doit être stable dans le temps.

    Un suffixe attribué au fil de la lecture dépendrait de l'ordre des lignes du
    COG ; une mise à jour pourrait alors échanger les URL de deux homonymes.
    """

    def test_slugs_uniques_inchanges(self) -> None:
        slugs = pd.Series(["paris", "lyon", "marseille"])
        deps = pd.Series(["75", "69", "13"])
        codes = pd.Series(["75056", "69123", "13055"])
        assert list(deduplicate_slugs(slugs, deps, codes)) == ["paris", "lyon", "marseille"]

    def test_homonymes_qualifies_par_departement(self) -> None:
        """Cas réel : Saint-Priest existe dans le Rhône et en Ardèche."""
        slugs = pd.Series(["saint-priest", "lyon", "saint-priest"])
        deps = pd.Series(["69", "69", "07"])
        codes = pd.Series(["69290", "69123", "07222"])
        result = deduplicate_slugs(slugs, deps, codes)

        assert list(result) == ["saint-priest-69", "lyon", "saint-priest-07"]

    def test_stable_quel_que_soit_l_ordre(self) -> None:
        """Inverser l'ordre des lignes ne doit pas changer les slugs attribués."""
        slugs = pd.Series(["saint-priest", "saint-priest"])
        deps = pd.Series(["69", "07"])
        codes = pd.Series(["69290", "07222"])
        direct = deduplicate_slugs(slugs, deps, codes)
        inverse = deduplicate_slugs(
            slugs[::-1].reset_index(drop=True),
            deps[::-1].reset_index(drop=True),
            codes[::-1].reset_index(drop=True),
        )

        assert direct.iloc[0] == inverse.iloc[1]
        assert direct.iloc[1] == inverse.iloc[0]

    def test_homonymes_du_meme_departement_departages_par_code_insee(self) -> None:
        slugs = pd.Series(["sainte-colombe", "sainte-colombe"])
        deps = pd.Series(["69", "69"])
        codes = pd.Series(["69175", "69999"])
        result = deduplicate_slugs(slugs, deps, codes)

        assert list(result) == ["sainte-colombe-69-69175", "sainte-colombe-69-69999"]

    def test_aucun_doublon_en_sortie(self) -> None:
        slugs = pd.Series(["a", "a", "b", "b", "b"])
        deps = pd.Series(["01", "02", "03", "03", "04"])
        codes = pd.Series(["01001", "02002", "03003", "03004", "04005"])
        result = deduplicate_slugs(slugs, deps, codes)

        assert len(result) == len(set(result))

    def test_slugs_en_minuscules(self) -> None:
        """Les codes de département corses sont en majuscules dans le COG."""
        slugs = pd.Series(["sainte-lucie", "sainte-lucie"])
        deps = pd.Series(["2A", "2B"])
        codes = pd.Series(["2A001", "2B002"])
        result = deduplicate_slugs(slugs, deps, codes)

        assert list(result) == ["sainte-lucie-2a", "sainte-lucie-2b"]
