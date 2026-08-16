"""Lot 9 — Chargement des agrégats dans Postgres (Supabase).

Lit la base SQLite produite par la transformation et la déverse dans Supabase,
dans le schéma `logement` — le projet héberge une autre application dans
`public`, et l'isolation évite toute collision de noms.
Le chargement est idempotent : chaque table est vidée puis réécrite dans une
transaction unique, de sorte qu'un échec en cours de route laisse la base dans
son état précédent plutôt qu'à moitié remplie.

Deux modes :
- avec `SUPABASE_DB_URL` dans l'environnement, écriture directe ;
- avec `--sql`, génération du script SQL sans connexion, pour relecture ou
  application par un autre canal.

Usage :
    python -m src.transform.load_supabase
    python -m src.transform.load_supabase --sql sortie.sql
"""

import argparse
import os
import sqlite3
from pathlib import Path

import pandas as pd

from src.common.config import DB_PATH, PARQUET_DIR
from src.common.logging import get_logger, timed_operation

logger = get_logger("transform.load_supabase")

# Les tables vivent dans leur propre schéma : le projet Supabase héberge une
# seconde application dans `public`, et l'isolation évite toute collision.
SCHEMA = "logement"

# Ordre imposé par les clés étrangères : les communes d'abord.
TABLES = [
    "communes",
    "agg_commune_immo",
    "agg_commune_prix",
    "agg_commune_dpe",
    "agg_commune_croisement",
    "agg_voie_immo",
    "fibre",
]

# Nombre de lignes par instruction INSERT. Assez grand pour limiter les
# allers-retours, assez petit pour que chaque instruction reste lisible et
# transportable.
LOT = 500


def litteral(valeur) -> str:
    """Rend une valeur littérale SQL, en échappant les apostrophes."""
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return "NULL"
    if isinstance(valeur, bool):
        return "TRUE" if valeur else "FALSE"
    if isinstance(valeur, (int, float)):
        return repr(valeur)
    return "'" + str(valeur).replace("'", "''") + "'"


def charger_tables(departement: str) -> dict[str, pd.DataFrame]:
    """Assemble les tables au schéma Postgres depuis SQLite et les Parquet."""
    con = sqlite3.connect(str(DB_PATH))

    communes = pd.read_sql("SELECT * FROM communes", con)
    eligibles = set(pd.read_sql("SELECT code_insee FROM communes_eligible", con)["code_insee"])
    communes["eligible"] = communes["code_insee"].isin(eligibles)

    # Le COG ne porte pas les codes postaux ; les diagnostics en portent un par
    # logement, on retient le plus fréquent de chaque commune.
    dpe = pd.read_parquet(
        PARQUET_DIR / "dpe" / f"dpe_dep{departement}.parquet",
        columns=["code_insee", "code_postal"],
    ).dropna()
    modes = dpe.groupby("code_insee")["code_postal"].agg(
        lambda s: s.mode().iloc[0] if len(s.mode()) else None,
    )
    communes["code_postal"] = communes["code_insee"].map(modes)

    communes = communes[[
        "code_insee", "nom", "slug", "code_departement", "nom_departement",
        "code_region", "nom_region", "code_postal", "population",
        "latitude", "longitude", "eligible",
    ]]

    tables = {"communes": communes}
    for nom in TABLES[1:-1]:
        tables[nom] = pd.read_sql(f"SELECT * FROM {nom}", con)
    con.close()

    fibre = pd.read_parquet(PARQUET_DIR / "fibre.parquet")
    connues = set(communes["code_insee"])
    # Le millésime ARCEP et le COG ne recensent pas exactement les mêmes
    # communes : sans ce filtre, la clé étrangère refuserait le lot entier.
    tables["fibre"] = fibre[fibre["code_insee"].isin(connues)][[
        "code_insee", "locaux", "locaux_ftth", "taux_fibre_pct", "population", "millesime",
    ]]

    return tables


def generer_sql(tables: dict[str, pd.DataFrame]) -> list[str]:
    instructions = ["BEGIN;"]

    # Suppression en ordre inverse des dépendances.
    for nom in reversed(TABLES):
        instructions.append(f"DELETE FROM {SCHEMA}.{nom};")

    for nom in TABLES:
        df = tables[nom]
        if df.empty:
            continue
        colonnes = ", ".join(df.columns)
        for debut in range(0, len(df), LOT):
            tranche = df.iloc[debut : debut + LOT]
            valeurs = ",\n".join(
                "(" + ", ".join(litteral(v) for v in ligne) + ")"
                for ligne in tranche.itertuples(index=False, name=None)
            )
            instructions.append(
                f"INSERT INTO {SCHEMA}.{nom} ({colonnes}) VALUES\n{valeurs};"
            )

    instructions.append("COMMIT;")
    return instructions


def main() -> None:
    parser = argparse.ArgumentParser(description="Chargement des agrégats dans Supabase")
    parser.add_argument("--departement", type=str, default="69")
    parser.add_argument(
        "--sql", type=str, default=None,
        help="Écrire le script SQL dans ce fichier au lieu de se connecter",
    )
    parser.add_argument(
        "--json", type=str, default=None,
        help="Écrire un fichier JSON par table dans ce répertoire, pour un "
             "chargement tiré par la base plutôt que poussé par le client",
    )
    args = parser.parse_args()

    with timed_operation(logger, "Chargement Supabase"):
        tables = charger_tables(args.departement)
        instructions = generer_sql(tables)

        logger.info("=== RAPPORT CHARGEMENT ===")
        for nom in TABLES:
            logger.info("  %-24s %6d lignes", nom, len(tables[nom]))
        logger.info("  %-24s %6d instructions", "script", len(instructions))

        if args.json:
            dossier = Path(args.json)
            dossier.mkdir(parents=True, exist_ok=True)
            for nom in TABLES:
                chemin = dossier / f"{nom}.json"
                tables[nom].to_json(chemin, orient="records", force_ascii=True)
                logger.info(
                    "  %-24s %s (%.0f Ko)",
                    nom, chemin.name, chemin.stat().st_size / 1024,
                )
            return

        if args.sql:
            Path(args.sql).write_text("\n\n".join(instructions), encoding="utf-8")
            logger.info("  Script écrit : %s", args.sql)
            return

        url = os.environ.get("SUPABASE_DB_URL")
        if not url:
            raise RuntimeError(
                "SUPABASE_DB_URL absent de l'environnement. "
                "Le renseigner, ou utiliser --sql pour produire le script."
            )

        import psycopg

        with psycopg.connect(url) as conn, conn.cursor() as cur:
            for instruction in instructions:
                cur.execute(instruction)
        logger.info("  Chargement terminé.")


if __name__ == "__main__":
    main()
