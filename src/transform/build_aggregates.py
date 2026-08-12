"""Lot 5 — Transformation et agrégats.

Lit les Parquet DVF et DPE, produit les tables d'agrégats via DuckDB,
applique la règle de couverture §7.4, écrit une base SQLite.

Usage :
    python -m src.transform.build_aggregates
    python -m src.transform.build_aggregates --departement 69
"""

import argparse
import sqlite3
from pathlib import Path

import duckdb

from src.common.config import (
    COVERAGE_MIN_DPE,
    COVERAGE_MIN_VENTES,
    DB_PATH,
    PARQUET_DIR,
)
from src.common.logging import get_logger, timed_operation
from src.transform.quality_checks import run_all_checks

logger = get_logger("transform.aggregates")

SQL_DIR = Path(__file__).resolve().parent.parent.parent / "sql"


def build(departement: str | None = None) -> None:
    con = duckdb.connect()

    with timed_operation(logger, "Chargement des Parquet"):
        dvf_path = str(PARQUET_DIR / "dvf" / "*.parquet")
        con.execute(f"CREATE TABLE dvf AS SELECT * FROM '{dvf_path}'")
        n_dvf = con.execute("SELECT COUNT(*) FROM dvf").fetchone()[0]
        logger.info("DVF chargé : %d lignes", n_dvf)

        dpe_path = str(PARQUET_DIR / "dpe" / "*.parquet")
        con.execute(f"CREATE TABLE dpe AS SELECT * FROM '{dpe_path}'")
        n_dpe = con.execute("SELECT COUNT(*) FROM dpe").fetchone()[0]
        logger.info("DPE chargé : %d lignes", n_dpe)

        communes_path = str(PARQUET_DIR / "communes.parquet")
        con.execute(
            f"CREATE TABLE communes AS SELECT * FROM '{communes_path}'"
        )
        n_communes = con.execute("SELECT COUNT(*) FROM communes").fetchone()[0]
        logger.info("Communes chargé : %d lignes", n_communes)

    if departement:
        con.execute(
            "DELETE FROM dvf WHERE code_departement != ?", [departement]
        )
        con.execute(
            "DELETE FROM dpe WHERE LEFT(code_insee, 2) != ? "
            "AND LEFT(code_insee, 3) != ?",
            [departement, departement],
        )
        logger.info("Filtré sur département %s", departement)

    with timed_operation(logger, "Agrégats immobiliers communaux"):
        sql = (SQL_DIR / "agg_commune_immo.sql").read_text()
        con.execute(f"CREATE TABLE agg_commune_immo AS {sql}")
        n = con.execute("SELECT COUNT(*) FROM agg_commune_immo").fetchone()[0]
        logger.info("  agg_commune_immo : %d lignes", n)

    with timed_operation(logger, "Agrégats DPE communaux"):
        sql = (SQL_DIR / "agg_commune_dpe.sql").read_text()
        con.execute(f"CREATE TABLE agg_commune_dpe AS {sql}")
        n = con.execute("SELECT COUNT(*) FROM agg_commune_dpe").fetchone()[0]
        logger.info("  agg_commune_dpe : %d lignes", n)

    with timed_operation(logger, "Règle de couverture §7.4"):
        eligible = con.execute(f"""
            SELECT c.code_insee, c.nom
            FROM communes c
            WHERE EXISTS (
                SELECT 1 FROM agg_commune_immo ai
                WHERE ai.code_insee = c.code_insee
                GROUP BY ai.code_insee
                HAVING SUM(nb_ventes) >= {COVERAGE_MIN_VENTES}
            )
            AND EXISTS (
                SELECT 1 FROM agg_commune_dpe ad
                WHERE ad.code_insee = c.code_insee
                GROUP BY ad.code_insee
                HAVING SUM(nb_logements) >= {COVERAGE_MIN_DPE}
            )
        """).fetchdf()

        n_eligible = len(eligible)
        logger.info(
            "Communes éligibles (>=%d ventes ET >=%d DPE) : %d / %d",
            COVERAGE_MIN_VENTES, COVERAGE_MIN_DPE, n_eligible, n_communes,
        )

        con.execute("""
            CREATE TABLE communes_eligible AS
            SELECT code_insee FROM eligible
        """)
        con.execute("CREATE TABLE eligible AS SELECT * FROM eligible")

    with timed_operation(logger, "Contrôles qualité"):
        run_all_checks(con)

    with timed_operation(logger, "Export SQLite"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DB_PATH.exists():
            DB_PATH.unlink()

        sqlite_con = sqlite3.connect(str(DB_PATH))

        for table in [
            "communes", "agg_commune_immo", "agg_commune_dpe",
        ]:
            df = con.execute(f"SELECT * FROM {table}").fetchdf()
            df.to_sql(table, sqlite_con, if_exists="replace", index=False)
            logger.info("  SQLite: %s — %d lignes", table, len(df))

        eligible_df = con.execute(
            "SELECT code_insee FROM communes_eligible"
        ).fetchdf()
        eligible_df.to_sql(
            "communes_eligible", sqlite_con, if_exists="replace", index=False,
        )

        sqlite_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_immo_commune "
            "ON agg_commune_immo(code_insee)"
        )
        sqlite_con.execute(
            "CREATE INDEX IF NOT EXISTS idx_dpe_commune "
            "ON agg_commune_dpe(code_insee)"
        )
        sqlite_con.commit()
        sqlite_con.close()

        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        logger.info("Base SQLite : %s (%.1f Mo)", DB_PATH, size_mb)
        if size_mb > 4000:
            logger.warning(
                "ATTENTION : base > 4 Go (limite D1 = 5 Go). "
                "Envisager une réduction du périmètre."
            )

    logger.info("=== RAPPORT TRANSFORMATION ===")
    logger.info("  Lignes DVF       : %d", n_dvf)
    logger.info("  Lignes DPE       : %d", n_dpe)
    logger.info("  Communes total   : %d", n_communes)
    logger.info("  Communes éligibles : %d", n_eligible)
    logger.info("  Base SQLite      : %.1f Mo", size_mb)

    con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transformation et agrégats",
    )
    parser.add_argument(
        "--departement", type=str, default=None,
        help="Filtrer sur un département",
    )
    args = parser.parse_args()

    with timed_operation(logger, "Build agrégats complet"):
        build(departement=args.departement)


if __name__ == "__main__":
    main()
