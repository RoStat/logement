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

from src.common.config import DVF_BASE_URL, PARQUET_DIR, PRIX_M2_MAX, PRIX_M2_MIN
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

# DVF compte une ligne par lot et non par vente : le seul filtre type_local
# retire déjà ~58 % du brut (267 716 lignes sur 462 796 pour le 69). La plage
# 40–70 % initialement retenue ne correspondait donc à aucune réalité.
RETENTION_MIN_PCT = 25
RETENTION_MAX_PCT = 40


def retention_pct(retenues: int, brutes: int) -> float:
    """Taux de rétention en pourcentage, borné par construction à [0, 100]."""
    if brutes <= 0:
        return 0.0
    return round(100 * retenues / brutes, 1)


def discover_available_years(session: requests.Session) -> list[int]:
    """Liste les années disponibles en interrogeant le répertoire distant."""
    logger.info("Découverte des années DVF disponibles sur %s", DVF_BASE_URL)
    resp = session.get(f"{DVF_BASE_URL}/", timeout=30)
    resp.raise_for_status()

    years = sorted(set(
        int(m) for m in re.findall(r'href="[^"]*?(\d{4})/?"', resp.text)
        if 2014 <= int(m) <= 2030
    ))
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


# Compteurs d'exclusion exprimés en lignes. Leur somme, augmentée des lignes
# retenues, doit reconstituer exactement le volume brut : c'est ce qui garantit
# qu'aucun filtre n'est laissé sans instrumentation.
EXCLUSION_KEYS = (
    "exclues_non_vente",
    "exclues_type_local",
    "exclues_prix_manquant",
    "exclues_surface_faible",
    "exclues_prix_m2_aberrant",
    "exclues_multi_lots",
)


def check_balance(stats: dict[str, int]) -> None:
    """Vérifie que exclusions + retenues = brut. Lève RuntimeError sinon."""
    total = sum(stats[k] for k in EXCLUSION_KEYS) + stats["lignes_retenues"]
    brut = stats["lignes_brutes"]
    if total != brut:
        detail = ", ".join(f"{k}={stats[k]}" for k in EXCLUSION_KEYS)
        raise RuntimeError(
            f"Bilan des filtres DVF incohérent : {total} comptabilisées pour "
            f"{brut} lignes brutes (écart {brut - total}). "
            f"Un filtre n'est pas instrumenté. Détail : {detail}, "
            f"lignes_retenues={stats['lignes_retenues']}"
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

    # Cessions à valeur symbolique (1 €, donations, ventes entre proches) :
    # elles passent mask_prix, qui n'écarte que les valeurs nulles ou négatives,
    # et suffisent à faire échouer le contrôle qualité sur la médiane communale.
    prix_m2 = df["valeur_fonciere"] / df["surface_reelle_bati"]
    mask_prix_m2 = prix_m2.between(PRIX_M2_MIN, PRIX_M2_MAX)

    df_filtered = df[
        mask_vente & mask_type & mask_prix & mask_surface & mask_prix_m2
    ].copy()

    multi_lots = (
        df_filtered.groupby("id_mutation")["id_parcelle"]
        .nunique()
        .reset_index()
    )
    multi_lots = multi_lots[multi_lots["id_parcelle"] > 1]["id_mutation"]
    n_mutations_multi = len(multi_lots)

    # Une mutation multi-lots porte plusieurs lignes : compter les mutations
    # sous-estimait l'exclusion et déséquilibrait le bilan.
    mask_multi = df_filtered["id_mutation"].isin(multi_lots)
    n_lignes_multi = int(mask_multi.sum())

    df_filtered = df_filtered[~mask_multi]

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
        "exclues_prix_m2_aberrant": int(
            (mask_vente & mask_type & mask_prix & mask_surface).sum()
            - (mask_vente & mask_type & mask_prix & mask_surface & mask_prix_m2).sum()
        ),
        "exclues_multi_lots": n_lignes_multi,
        "lignes_retenues": n_retained,
        "mutations_multi_lots": n_mutations_multi,
        "taux_retention_pct": retention_pct(n_retained, n_raw),
    }

    check_balance(stats)

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

            # taux_retention_pct est un ratio : le cumuler n'a pas de sens
            # (il atteignait 155,6 % sur 5 années). Recalculé sur les totaux.
            for k, v in stats.items():
                if k == "taux_retention_pct":
                    continue
                total_stats[k] = total_stats.get(k, 0) + v

            out_path = PARQUET_DIR / "dvf" / f"dvf_{annee}.parquet"
            write_parquet(df_clean, out_path)
            logger.info(
                "  %s : %d → %d lignes (rétention %.1f%%)",
                annee, stats["lignes_brutes"], stats["lignes_retenues"],
                stats["taux_retention_pct"],
            )

    if total_stats:
        total_stats["taux_retention_pct"] = retention_pct(
            total_stats.get("lignes_retenues", 0), total_stats.get("lignes_brutes", 0),
        )
        check_balance(total_stats)

    logger.info("=== RAPPORT DVF ===")
    for k, v in total_stats.items():
        logger.info("  %-30s %s", k, v)

    if total_stats:
        taux = total_stats["taux_retention_pct"]
        logger.info("  Taux de rétention global : %.1f%%", taux)
        if not RETENTION_MIN_PCT <= taux <= RETENTION_MAX_PCT:
            logger.warning(
                "ATTENTION : taux de rétention (%.1f%%) hors de la plage attendue "
                "(%d–%d%%). Investiguer avant de poursuivre.",
                taux, RETENTION_MIN_PCT, RETENTION_MAX_PCT,
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
