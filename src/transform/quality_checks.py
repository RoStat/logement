"""Contrôles qualité bloquants sur les agrégats (§5 du cahier des charges)."""

import duckdb

from src.common.logging import get_logger

logger = get_logger("transform.quality")


def run_all_checks(con: duckdb.DuckDBPyConnection) -> None:
    """Exécute tous les contrôles qualité. Lève RuntimeError au premier échec."""
    _check_prix_m2_bounds(con)
    _check_part_pct_bounds(con)
    _check_part_pct_sum(con)
    _check_communes_departement(con)
    logger.info("Tous les contrôles qualité passent.")


def _check_prix_m2_bounds(con: duckdb.DuckDBPyConnection) -> None:
    result = con.execute("""
        SELECT code_insee, annee, type_local, prix_m2_median
        FROM agg_commune_immo
        WHERE prix_m2_median < 100 OR prix_m2_median > 40000
    """).fetchall()
    if result:
        samples = result[:10]
        raise RuntimeError(
            f"prix_m2_median hors bornes [100, 40000] : "
            f"{len(result)} lignes. Exemples : {samples}"
        )
    logger.info("  prix_m2_median : bornes OK")


def _check_part_pct_bounds(con: duckdb.DuckDBPyConnection) -> None:
    result = con.execute("""
        SELECT code_insee, classe_dpe, part_pct
        FROM agg_commune_dpe
        WHERE part_pct < 0 OR part_pct > 100
    """).fetchall()
    if result:
        raise RuntimeError(
            f"part_pct hors [0, 100] : {len(result)} lignes. "
            f"Exemples : {result[:10]}"
        )
    logger.info("  part_pct : bornes OK")


def _check_part_pct_sum(con: duckdb.DuckDBPyConnection) -> None:
    result = con.execute("""
        SELECT code_insee, SUM(part_pct) AS total
        FROM agg_commune_dpe
        GROUP BY code_insee
        HAVING ABS(SUM(part_pct) - 100) > 0.5
    """).fetchall()
    if result:
        raise RuntimeError(
            f"Somme des part_pct != 100 (±0.5) pour {len(result)} communes. "
            f"Exemples : {result[:10]}"
        )
    logger.info("  part_pct somme : OK")


def _check_communes_departement(con: duckdb.DuckDBPyConnection) -> None:
    result = con.execute("""
        SELECT a.code_insee
        FROM agg_commune_immo a
        LEFT JOIN communes c ON a.code_insee = c.code_insee
        WHERE c.code_departement IS NULL
    """).fetchall()
    if result:
        raise RuntimeError(
            f"{len(result)} communes dans les agrégats sans département rattaché. "
            f"Exemples : {[r[0] for r in result[:10]]}"
        )
    logger.info("  communes/département : OK")
