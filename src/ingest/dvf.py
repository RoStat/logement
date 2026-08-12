"""Lot 1 — Ingestion DVF géolocalisé (transactions immobilières).

Télécharge les données DVF depuis files.data.gouv.fr, valide le schéma,
filtre les ventes exploitables et écrit en Parquet partitionné par année.

Usage :
    python -m src.ingest.dvf --departement 69
    python -m src.ingest.dvf --departement 69 --annees 2023,2024,2025
    python -m src.ingest.dvf --all
"""

import argparse
import io
import re

import pandas as pd
import requests

from src.common.config import DVF_BASE_URL, PARQUET_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.dvf")

EXPECTED_COLUMNS = {
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_nom_voie",
    "adresse_code_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "code_departement",
    "id_parcelle",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
    "surface_terrain",
    "longitude",
    "latitude",
}

VALID_TYPES_LOCAL = {"Maison", "Appartement"}


def discover_available_years(session: requests.Session) -> list[int]:
    """Liste les années disponibles en interrogeant le répertoire distant."""
    logger.info("Découverte des années DVF disponibles sur %s", DVF_BASE_URL)
    resp = session.get(f"{DVF_BASE_URL}/", timeout=30)
    resp.raise_for_status()

    years = sorted(set(int(m) for m in re.findall(r'href="(\d{4})/"', resp.text)))
    if not years:
        raise RuntimeError(
            f"Aucune année trouvée dans le listing de {DVF_BASE_URL}. "
            f"Contenu reçu (500 premiers caractères) : {resp.text[:500]}"
        )
    logger.info("Années disponibles : %s", years)
    return years


def download_dvf_departement(
    session: requests.Session, annee: int, departement: str
) -> pd.DataFrame:
    """Télécharge le CSV DVF pour un département et une année."""
    url = f"{DVF_BASE_URL}/{annee}/departements/{departement}.csv.gz"
    logger.info("Téléchargement %s", url)

    resp = session.get(url, timeout=300)
    resp.raise_for_status()

    df = pd.read_csv(
        io.BytesIO(resp.content),
        compression="gzip",
        dtype=str,
        low_memory=False,
    )
    return df


def download_dvf_full(session: requests.Session, annee: int) -> pd.DataFrame:
    """Télécharge le CSV DVF complet pour une année."""
    url = f"{DVF_BASE_URL}/{annee}/full.csv.gz"
    logger.info("Téléchargement %s (fichier complet, peut prendre plusieurs minutes)", url)

    resp = session.get(url, timeout=600, stream=True)
    resp.raise_for_status()

    chunks = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
        chunks.append(chunk)
        downloaded += len(chunk)
        logger.info("  %d Mo téléchargés…", downloaded // (1024 * 1024))

    content = b"".join(chunks)
    df = pd.read_csv(
        io.BytesIO(content),
        compression="gzip",
        dtype=str,
        low_memory=False,
    )
    return df


def validate_schema(df: pd.DataFrame) -> None:
    """Vérifie que toutes les colonnes attendues sont présentes. Arrêt si manquante."""
    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Colonnes DVF manquantes : {missing}. "
            f"Colonnes présentes : {sorted(df.columns)}"
        )


def filter_dvf(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Applique les filtres du cahier des charges et retourne (df filtré, stats)."""
    n_raw = len(df)

    df["valeur_fonciere"] = (
        df["valeur_fonciere"].str.replace(",", ".", regex=False).astype(float, errors="ignore")
    )
    df["valeur_fonciere"] = pd.to_numeric(df["valeur_fonciere"], errors="coerce")
    df["surface_reelle_bati"] = pd.to_numeric(df["surface_reelle_bati"], errors="coerce")

    mask_vente = df["nature_mutation"] == "Vente"
    mask_type = df["type_local"].isin(VALID_TYPES_LOCAL)
    mask_prix = df["valeur_fonciere"].notna() & (df["valeur_fonciere"] > 0)
    mask_surface = df["surface_reelle_bati"] > 9

    df_filtered = df[mask_vente & mask_type & mask_prix & mask_surface].copy()

    multi_lots = (
        df_filtered.groupby("id_mutation")["id_parcelle"]
        .nunique()
        .reset_index()
    )
    multi_lots = multi_lots[multi_lots["id_parcelle"] > 1]["id_mutation"]
    n_multi = len(multi_lots)

    df_filtered = df_filtered[~df_filtered["id_mutation"].isin(multi_lots)]

    n_retained = len(df_filtered)

    stats = {
        "lignes_brutes": n_raw,
        "exclues_non_vente": int((~mask_vente).sum()),
        "exclues_type_local": int(mask_vente.sum() - (mask_vente & mask_type).sum()),
        "exclues_prix_manquant": int(
            (mask_vente & mask_type).sum()
            - (mask_vente & mask_type & mask_prix).sum()
        ),
        "exclues_surface_faible": int(
            (mask_vente & mask_type & mask_prix).sum()
            - (mask_vente & mask_type & mask_prix & mask_surface).sum()
        ),
        "exclues_multi_lots": n_multi,
        "lignes_retenues": n_retained,
        "taux_retention_pct": round(100 * n_retained / n_raw, 1) if n_raw > 0 else 0,
    }

    return df_filtered, stats


def run_ingestion(
    departement: str | None,
    annees: list[int] | None,
    all_france: bool,
) -> None:
    session = requests.Session()
    available_years = discover_available_years(session)

    if annees:
        years = [y for y in annees if y in available_years]
        missing_years = [y for y in annees if y not in available_years]
        if missing_years:
            logger.warning("Années demandées mais absentes : %s", missing_years)
    else:
        years = available_years[-5:]

    logger.info("Années retenues : %s", years)

    total_stats: dict[str, int] = {}

    for annee in years:
        with timed_operation(logger, f"DVF {annee}"):
            if all_france:
                df = download_dvf_full(session, annee)
            else:
                dept = departement or "69"
                df = download_dvf_departement(session, annee, dept)

            validate_schema(df)
            df_clean, stats = filter_dvf(df)

            for k, v in stats.items():
                total_stats[k] = total_stats.get(k, 0) + v

            out_path = PARQUET_DIR / "dvf" / f"dvf_{annee}.parquet"
            write_parquet(df_clean, out_path)
            logger.info(
                "  %s : %d → %d lignes (rétention %.1f%%)",
                annee, stats["lignes_brutes"], stats["lignes_retenues"],
                stats["taux_retention_pct"],
            )

    logger.info("=== RAPPORT DVF ===")
    for k, v in total_stats.items():
        logger.info("  %-30s %s", k, v)

    n_years = len(years)
    if n_years > 0:
        n_ret = total_stats.get("lignes_retenues", 0)
        n_brut = max(total_stats.get("lignes_brutes", 1), 1)
        avg_retention = round(100 * n_ret / n_brut, 1)
        logger.info("  Taux de rétention global : %.1f%%", avg_retention)
        if avg_retention < 40 or avg_retention > 70:
            logger.warning(
                "ATTENTION : taux de rétention (%.1f%%) hors de la plage attendue (40–70%%). "
                "Investiguer avant de poursuivre.",
                avg_retention,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion DVF géolocalisé")
    parser.add_argument(
        "--departement", type=str, default="69", help="Code département (défaut : 69)",
    )
    parser.add_argument(
        "--annees", type=str, default=None, help="Années, virgule (ex: 2023,2024)",
    )
    parser.add_argument("--all", action="store_true", help="Télécharger la France entière")
    args = parser.parse_args()

    annees = [int(a) for a in args.annees.split(",")] if args.annees else None

    with timed_operation(logger, "Ingestion DVF"):
        run_ingestion(
            departement=args.departement,
            annees=annees,
            all_france=args.all,
        )


if __name__ == "__main__":
    main()
