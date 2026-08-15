"""Lot 2 — Ingestion DPE logements existants (ADEME).

Télécharge les DPE via l'API data.ademe.fr avec pagination par curseur,
limitation de débit à 5 req/s, et reprise sur incident.

Usage :
    python -m src.ingest.dpe --code-postal 69100
    python -m src.ingest.dpe --departement 69
    python -m src.ingest.dpe --all
"""

import argparse
import json
import time

import pandas as pd
import requests

from src.common.config import DATA_DIR, DPE_API_URL, DPE_PAGE_SIZE, DPE_RATE_LIMIT, PARQUET_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.dpe")

SELECT_COLUMNS = [
    "N°DPE",
    "Code_postal_(BAN)",
    "Code_INSEE_(BAN)",
    "Adresse_(BAN)",
    "Etiquette_DPE",
    "Etiquette_GES",
    "Conso_5_usages_é_finale",
    "Surface_habitable_logement",
    "Année_construction",
    "Date_établissement_DPE",
    "Coordonnée_cartographique_X_(BAN)",
    "Coordonnée_cartographique_Y_(BAN)",
]

COLUMN_RENAME = {
    "N°DPE": "numero_dpe",
    "Code_postal_(BAN)": "code_postal",
    "Code_INSEE_(BAN)": "code_insee",
    "Adresse_(BAN)": "adresse",
    "Etiquette_DPE": "classe_dpe",
    "Etiquette_GES": "classe_ges",
    "Conso_5_usages_é_finale": "conso_energie",
    "Surface_habitable_logement": "surface_habitable",
    "Année_construction": "annee_construction",
    "Date_établissement_DPE": "date_etablissement",
    "Coordonnée_cartographique_X_(BAN)": "longitude",
    "Coordonnée_cartographique_Y_(BAN)": "latitude",
}

# Caracteres reserves par la syntaxe query_string d'Elasticsearch, utilisee par
# le parametre `qs` de data-fair. Les noms de champs de l'API ADEME comportent
# des parentheses : sans echappement elles sont interpretees comme un
# groupement et le filtre est silencieusement ignore (toute la France est
# alors telechargee).
QS_SPECIAL_CHARS = set(r'+-=&|><!(){}[]^"~*?:\/')

FIELD_CODE_POSTAL = "Code_postal_(BAN)"
FIELD_CODE_INSEE = "Code_INSEE_(BAN)"


def escape_qs(value: str) -> str:
    """Echappe les caracteres reserves de la syntaxe query_string."""
    return "".join("\\" + c if c in QS_SPECIAL_CHARS else c for c in value)


def build_query_params(
    code_postal: str | None = None,
    departement: str | None = None,
    cursor: str | None = None,
) -> dict[str, str | int]:
    """Construit les parametres d'une requete de page a l'API ADEME.

    Le filtre departement s'appuie sur le code INSEE et non sur le code postal :
    le prefixe du code INSEE communal designe le departement de facon fiable
    (69xxx = Rhone), ce qui n'est pas vrai des codes postaux, dont le prefixe
    deborde sur les departements voisins.
    """
    if code_postal and departement:
        raise ValueError(
            "Filtres incompatibles : preciser --code-postal ou --departement, pas les deux."
        )

    params: dict[str, str | int] = {
        "size": DPE_PAGE_SIZE,
        "select": ",".join(SELECT_COLUMNS),
    }

    if code_postal:
        params["qs"] = f"{escape_qs(FIELD_CODE_POSTAL)}:{escape_qs(code_postal)}"
    elif departement:
        # Le `*` final est volontairement laisse non echappe : c'est le joker.
        params["qs"] = f"{escape_qs(FIELD_CODE_INSEE)}:{escape_qs(departement)}*"

    if cursor:
        params["after"] = cursor

    return params


CURSOR_FILE = DATA_DIR / "raw" / "dpe" / "_cursor.json"


def save_cursor(cursor: str, page: int, total_rows: int) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps({
        "cursor": cursor,
        "page": page,
        "total_rows": total_rows,
    }))


def load_cursor() -> tuple[str | None, int, int]:
    if CURSOR_FILE.exists():
        data = json.loads(CURSOR_FILE.read_text())
        logger.info(
            "Reprise depuis le curseur page %d (%d lignes)",
            data["page"], data["total_rows"],
        )
        return data["cursor"], data["page"], data["total_rows"]
    return None, 0, 0


def clear_cursor() -> None:
    if CURSOR_FILE.exists():
        CURSOR_FILE.unlink()


def fetch_page(
    session: requests.Session,
    code_postal: str | None = None,
    departement: str | None = None,
    cursor: str | None = None,
) -> tuple[list[dict], str | None]:
    """Récupère une page de résultats DPE. Retourne (lignes, prochain curseur)."""
    params = build_query_params(
        code_postal=code_postal, departement=departement, cursor=cursor,
    )

    resp = session.get(DPE_API_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("results", [])

    next_url = data.get("next")
    next_cursor = None
    if next_url:
        import urllib.parse
        parsed = urllib.parse.urlparse(next_url)
        qs = urllib.parse.parse_qs(parsed.query)
        next_cursor = qs.get("after", [None])[0]

    return rows, next_cursor


def run_ingestion(
    code_postal: str | None,
    all_france: bool,
    resume: bool,
    departement: str | None = None,
) -> None:
    session = requests.Session()

    cursor, start_page, accumulated_rows = (None, 0, 0)
    if resume:
        cursor, start_page, accumulated_rows = load_cursor()

    all_rows: list[dict] = []
    page = start_page
    min_interval = 1.0 / DPE_RATE_LIMIT

    if code_postal:
        filter_label = f"code postal {code_postal}"
    elif departement:
        filter_label = f"département {departement}"
    else:
        filter_label = "France entière"
    logger.info("Ingestion DPE : %s (page de départ : %d)", filter_label, page)

    if not all_france and not code_postal and not departement:
        raise RuntimeError(
            "Préciser --code-postal, --departement ou --all. "
            "Ne jamais lancer un téléchargement complet par accident."
        )

    with timed_operation(logger, f"Ingestion DPE {filter_label}"):
        while True:
            t0 = time.monotonic()
            rows, next_cursor = fetch_page(
                session,
                code_postal=code_postal,
                departement=departement,
                cursor=cursor,
            )
            page += 1

            if not rows:
                logger.info("Page %d : aucun résultat — fin de la pagination", page)
                break

            all_rows.extend(rows)
            total = accumulated_rows + len(all_rows)

            if page % 10 == 0:
                logger.info("  Page %d : %d lignes cumulées", page, total)
                save_cursor(cursor or "", page, total)

            if not next_cursor:
                logger.info("Page %d : pas de curseur suivant — fin de la pagination", page)
                break

            cursor = next_cursor

            elapsed = time.monotonic() - t0
            wait = max(0, min_interval - elapsed)
            if wait > 0:
                time.sleep(wait)

    if not all_rows:
        logger.warning("Aucune donnée DPE récupérée pour %s", filter_label)
        return

    df = pd.DataFrame(all_rows)

    missing = set(SELECT_COLUMNS) - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Colonnes DPE manquantes dans la réponse API : {missing}. "
            f"Colonnes reçues : {sorted(df.columns)}"
        )

    df = df.rename(columns=COLUMN_RENAME)

    for col in ["conso_energie", "surface_habitable"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["annee_construction"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    suffix = code_postal or (f"dep{departement}" if departement else "full")
    out_path = PARQUET_DIR / "dpe" / f"dpe_{suffix}.parquet"
    write_parquet(df, out_path)
    clear_cursor()

    logger.info("=== RAPPORT DPE ===")
    logger.info("  Périmètre          : %s", filter_label)
    logger.info("  Pages parcourues   : %d", page)
    logger.info("  Lignes récupérées  : %d", len(df))
    logger.info("  Fichier            : %s", out_path)

    logger.info("  Colonnes API (noms originaux) :")
    for col in SELECT_COLUMNS:
        logger.info("    - %s", col)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion DPE logements existants (ADEME)")
    perimetre = parser.add_mutually_exclusive_group(required=True)
    perimetre.add_argument(
        "--code-postal", type=str, default=None, help="Code postal (dev)",
    )
    perimetre.add_argument(
        "--departement", type=str, default=None,
        help="Département, filtré sur le préfixe du code INSEE (ex: 69)",
    )
    perimetre.add_argument(
        "--all", action="store_true", help="Télécharger tous les DPE",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Reprendre depuis le curseur",
    )
    args = parser.parse_args()

    run_ingestion(
        code_postal=args.code_postal,
        departement=args.departement,
        all_france=args.all,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
