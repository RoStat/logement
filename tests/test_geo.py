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
    def test_no_duplicates(self) -> None:
        slugs = pd.Series(["paris", "lyon", "marseille"])
        result = deduplicate_slugs(slugs)
        assert list(result) == ["paris", "lyon", "marseille"]

    def test_with_duplicates(self) -> None:
        slugs = pd.Series(["saint-pierre", "lyon", "saint-pierre", "saint-pierre"])
        result = deduplicate_slugs(slugs)
        assert result.iloc[0] == "saint-pierre"
        assert result.iloc[2] == "saint-pierre-1"
        assert result.iloc[3] == "saint-pierre-2"

    def test_all_unique(self) -> None:
        slugs = pd.Series(["a", "a", "b", "b", "b"])
        result = deduplicate_slugs(slugs)
        assert len(result) == len(set(result))
