"""Lot 2 — Ingestion DPE logements existants (ADEME).

Télécharge les DPE via l'API data.ademe.fr avec pagination par curseur,
limitation de débit à 5 req/s, et reprise sur incident.

Usage :
    python -m src.ingest.dpe --schema          # lister les champs disponibles
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

# Schéma du jeu `dpe03existant` — noms de champs confirmés par appel réel.
# L'ancien jeu `dpe-v2-logements-existants` renvoie 404 et ses noms de champs
# accentués et parenthésés n'ont plus cours.
SELECT_COLUMNS = [
    "numero_dpe",
    "identifiant_ban",
    "code_insee_ban",
    "code_departement_ban",
    "code_postal_ban",
    "nom_commune_ban",
    "nom_rue_ban",
    "numero_voie_ban",
    "adresse_ban",
    "etiquette_dpe",
    "etiquette_ges",
    "conso_5_usages_par_m2_ep",
    "surface_habitable_logement",
    "date_etablissement_dpe",
    "periode_construction",
    "type_batiment",
    "statut_geocodage",
    "score_ban",
    "_geopoint",
]

COLUMN_RENAME = {
    "numero_dpe": "numero_dpe",
    "identifiant_ban": "identifiant_ban",
    "code_insee_ban": "code_insee",
    "code_departement_ban": "code_departement",
    "code_postal_ban": "code_postal",
    "nom_commune_ban": "nom_commune",
    "nom_rue_ban": "nom_rue",
    "numero_voie_ban": "numero_voie",
    "adresse_ban": "adresse",
    "etiquette_dpe": "classe_dpe",
    "etiquette_ges": "classe_ges",
    "conso_5_usages_par_m2_ep": "conso_energie",
    "surface_habitable_logement": "surface_habitable",
    "date_etablissement_dpe": "date_etablissement",
    "periode_construction": "periode_construction",
    "type_batiment": "type_batiment",
    "statut_geocodage": "statut_geocodage",
    "score_ban": "score_ban",
    "_geopoint": "geopoint",
}

# Colonnes à convertir en numérique après renommage.
NUMERIC_COLUMNS = ["conso_energie", "surface_habitable", "score_ban"]

# Champ de filtrage : le jeu expose directement le département, il n'y a donc
# aucune raison de le déduire d'un préfixe de code INSEE.
FIELD_CODE_POSTAL = "code_postal_ban"
FIELD_CODE_DEPARTEMENT = "code_departement_ban"

SCHEMA_URL = DPE_API_URL.rsplit("/", 1)[0] + "/schema"


def fetch_schema(session: requests.Session) -> list[dict]:
    """Récupère le schéma du jeu de données ADEME.

    Sert à vérifier les noms de champs disponibles sans lancer d'ingestion :
    l'identifiant du jeu et ses champs ont déjà changé une fois.
    """
    resp = session.get(SCHEMA_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def build_query_params(
    code_postal: str | None = None,
    departement: str | None = None,
    cursor: str | None = None,
) -> dict[str, str | int]:
    """Construit les paramètres d'une requête de page à l'API ADEME."""
    if code_postal and departement:
        raise ValueError(
            "Filtres incompatibles : préciser --code-postal ou --departement, pas les deux."
        )

    params: dict[str, str | int] = {
        "size": DPE_PAGE_SIZE,
        "select": ",".join(SELECT_COLUMNS),
    }

    if code_postal:
        params["qs"] = f"{FIELD_CODE_POSTAL}:{code_postal}"
    elif departement:
        params["qs"] = f"{FIELD_CODE_DEPARTEMENT}:{departement}"

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

    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

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


def report_schema() -> None:
    """Affiche les champs exposés par le jeu et signale ceux non sélectionnés."""
    session = requests.Session()
    schema = fetch_schema(session)
    champs = sorted(f.get("key", "") for f in schema)

    logger.info("=== SCHÉMA %s ===", SCHEMA_URL)
    logger.info("  %d champs exposés", len(champs))
    for champ in champs:
        marque = "*" if champ in SELECT_COLUMNS else " "
        logger.info("  %s %s", marque, champ)

    manquants = [c for c in SELECT_COLUMNS if c not in champs]
    if manquants:
        logger.error("Champs sélectionnés absents du schéma : %s", manquants)

    surfaces = [c for c in champs if "surface" in c.lower()]
    logger.info("  Champs de surface disponibles : %s", surfaces or "aucun")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion DPE logements existants (ADEME)")
    perimetre = parser.add_mutually_exclusive_group(required=True)
    perimetre.add_argument(
        "--schema", action="store_true",
        help="Afficher les champs du jeu de données et quitter",
    )
    perimetre.add_argument(
        "--code-postal", type=str, default=None, help="Code postal (dev)",
    )
    perimetre.add_argument(
        "--departement", type=str, default=None,
        help="Département, filtré sur code_departement_ban (ex: 69)",
    )
    perimetre.add_argument(
        "--all", action="store_true", help="Télécharger tous les DPE",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Reprendre depuis le curseur",
    )
    args = parser.parse_args()

    if args.schema:
        report_schema()
        return

    run_ingestion(
        code_postal=args.code_postal,
        departement=args.departement,
        all_france=args.all,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
