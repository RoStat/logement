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

# Colonnes identifiant une ligne-lot : DVF en republie certaines à l'identique.
DEDUP_KEYS = [
    "id_mutation",
    "id_parcelle",
    "type_local",
    "valeur_fonciere",
    "surface_reelle_bati",
]

# Le taux rapporte les mutations retenues aux lignes brutes. DVF émettant une
# ligne par lot, le seul filtre type_local retire déjà ~58 % du brut, et le
# regroupement par mutation ~12 % du reliquat. Mesure sur le 69 : 22,8 %.
# Plage à réexaminer à l'ingestion d'un second département : les zones rurales,
# plus riches en maisons et plus pauvres en ventes multi-lots, retiendront plus.
RETENTION_MIN_PCT = 18
RETENTION_MAX_PCT = 32


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


# Compteurs d'exclusion exprimés en LIGNES. Leur somme, augmentée des doublons
# et des lignes parvenues au regroupement, doit reconstituer le volume brut :
# c'est ce qui garantit qu'aucun filtre n'est laissé sans instrumentation.
EXCLUSION_KEYS = (
    "exclues_non_vente",
    "exclues_type_local",
    "exclues_prix_manquant",
    "exclues_surface_faible",
    "exclues_multi_lots",
    "doublons_stricts",
    "exclues_type_mixte",
)


def check_balance(stats: dict[str, int]) -> None:
    """Vérifie les deux bilans, en lignes puis en mutations.

    Le regroupement fait changer l'unité de compte en cours de traitement : les
    deux bilans doivent être vérifiés séparément, sous peine de comparer des
    lignes à des mutations.
    """
    total = sum(stats[k] for k in EXCLUSION_KEYS) + stats["lignes_regroupees"]
    brut = stats["lignes_brutes"]
    if total != brut:
        detail = ", ".join(f"{k}={stats[k]}" for k in EXCLUSION_KEYS)
        raise RuntimeError(
            f"Bilan des filtres DVF incohérent : {total} lignes comptabilisées "
            f"pour {brut} brutes (écart {brut - total}). "
            f"Un filtre n'est pas instrumenté. Détail : {detail}, "
            f"lignes_regroupees={stats['lignes_regroupees']}"
        )

    formees = stats["mutations_formees"]
    attendu = formees - stats["exclues_prix_m2_aberrant"]
    if attendu != stats["mutations_retenues"]:
        raise RuntimeError(
            f"Bilan des mutations incohérent : {formees} formées moins "
            f"{stats['exclues_prix_m2_aberrant']} aberrantes donnent {attendu}, "
            f"mais {stats['mutations_retenues']} sont retenues."
        )


def _regrouper_par_mutation(df: pd.DataFrame) -> pd.DataFrame:
    """Réduit les lignes-lots à une ligne par mutation.

    La surface bâtie est sommée sur les lots d'habitation retenus ; la valeur
    foncière, qui porte déjà sur la mutation entière, est reprise telle quelle.
    Commune et date sont invariantes au sein d'une mutation (vérifié sur le 69).
    """
    agregations = {
        col: "first" for col in df.columns if col not in ("id_mutation", "surface_reelle_bati")
    }
    agregations["surface_reelle_bati"] = "sum"

    mutations = df.groupby("id_mutation", as_index=False).agg(agregations)
    mutations["nb_lots"] = (
        df.groupby("id_mutation", as_index=False).size()["size"].to_numpy()
    )
    return mutations


def filter_dvf(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Applique les filtres du cahier des charges et retourne (mutations, stats).

    L'unité d'observation est la **mutation**, pas la ligne : DVF émet une ligne
    par lot, toutes porteuses de la valeur foncière totale. Compter les lignes
    gonflait le nombre de ventes et surestimait le prix au m² — jusqu'à 12 % sur
    les communes à grosses ventes multi-lots.
    """
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

    # Détection multi-lots sur le fichier BRUT, et non sur le sous-ensemble déjà
    # filtré : le caractère multi-lots est une propriété de la mutation telle
    # qu'enregistrée, pas du reliquat qui survit aux filtres.
    parcelles_par_mutation = df.groupby("id_mutation")["id_parcelle"].nunique()
    mutations_multi = parcelles_par_mutation[parcelles_par_mutation > 1].index
    n_mutations_multi = len(mutations_multi)
    mask_mono_parcelle = ~df["id_mutation"].isin(mutations_multi)

    cumul = mask_vente
    n_non_vente = int((~mask_vente).sum())
    n_type = int(cumul.sum() - (cumul & mask_type).sum())
    cumul = cumul & mask_type
    n_prix = int(cumul.sum() - (cumul & mask_prix).sum())
    cumul = cumul & mask_prix
    n_surface = int(cumul.sum() - (cumul & mask_surface).sum())
    cumul = cumul & mask_surface
    n_multi = int(cumul.sum() - (cumul & mask_mono_parcelle).sum())
    cumul = cumul & mask_mono_parcelle

    eligibles = df[cumul].copy()

    # DVF republie certaines lignes à l'identique.
    n_avant_dedup = len(eligibles)
    eligibles = eligibles.drop_duplicates(subset=DEDUP_KEYS)
    n_doublons = n_avant_dedup - len(eligibles)

    # Une mutation mêlant maison et appartement ne permet pas d'attribuer la
    # valeur foncière à l'un ou l'autre : même raison que le multi-parcelles.
    types_par_mutation = eligibles.groupby("id_mutation")["type_local"].nunique()
    mutations_mixtes = types_par_mutation[types_par_mutation > 1].index
    mask_mixte = eligibles["id_mutation"].isin(mutations_mixtes)
    n_lignes_mixtes = int(mask_mixte.sum())
    eligibles = eligibles[~mask_mixte]

    n_lignes_regroupees = len(eligibles)
    mutations = _regrouper_par_mutation(eligibles)
    n_mutations_formees = len(mutations)

    # Cessions à valeur symbolique (1 €, donations, ventes entre proches) : elles
    # passent mask_prix, qui n'écarte que les valeurs nulles ou négatives. Le
    # contrôle porte sur la mutation regroupée, seule surface complète connue.
    prix_m2 = mutations["valeur_fonciere"] / mutations["surface_reelle_bati"]
    mask_prix_m2 = prix_m2.between(PRIX_M2_MIN, PRIX_M2_MAX)
    n_aberrants = int((~mask_prix_m2).sum())

    result = mutations[mask_prix_m2].copy()
    n_retenues = len(result)

    stats = {
        "lignes_brutes": n_raw,
        "exclues_non_vente": n_non_vente,
        "exclues_type_local": n_type,
        "exclues_prix_manquant": n_prix,
        "exclues_surface_faible": n_surface,
        "exclues_multi_lots": n_multi,
        "doublons_stricts": n_doublons,
        "exclues_type_mixte": n_lignes_mixtes,
        "lignes_regroupees": n_lignes_regroupees,
        "mutations_formees": n_mutations_formees,
        "exclues_prix_m2_aberrant": n_aberrants,
        "mutations_retenues": n_retenues,
        "mutations_multi_lots": n_mutations_multi,
        "taux_retention_pct": retention_pct(n_retenues, n_raw),
    }

    check_balance(stats)

    return result, stats


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
                "  %s : %d lignes → %d mutations (rétention %.1f%%)",
                annee, stats["lignes_brutes"], stats["mutations_retenues"],
                stats["taux_retention_pct"],
            )

    if total_stats:
        total_stats["taux_retention_pct"] = retention_pct(
            total_stats.get("mutations_retenues", 0), total_stats.get("lignes_brutes", 0),
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
