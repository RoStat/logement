"""Lot 10 — Loyers d'annonce par commune (« Carte des loyers »).

DVF ne décrit que des ventes. Sans loyers, impossible de répondre à la question
que se pose le lecteur du site : « je paie tant en location, est-ce que j'ai
intérêt à acheter ici ? »

Le Ministère de la Transition écologique publie chaque année une estimation du
loyer au m² par commune, issue d'un modèle appliqué aux annonces.

**Le champ `TYPPRED` est déterminant** : `commune` signale une estimation
appuyée sur des annonces observées dans la commune, `maille` une extrapolation
depuis une zone plus large. Afficher la seconde comme une mesure locale serait
trompeur ; elle est donc conservée et signalée telle quelle.

Usage :
    python -m src.ingest.loyers --departement 69
"""

import argparse
import io

import pandas as pd
import requests

from src.common.config import PARQUET_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.loyers")

RECHERCHE_URL = "https://www.data.gouv.fr/api/1/datasets/"
REQUETE = "carte des loyers"

# Les fichiers sont publiés en latin-1, comme souvent sur ce portail.
ENCODAGE = "latin-1"

# Intitulé de ressource → catégorie de bien retenue en sortie.
CATEGORIES = {
    "indicateurs de loyer appartement": "appartement",
    "indicateur de loyer appartement de 1 ou 2 pièces": "appartement_1_2_pieces",
    "indicateurs de loyer appartement de 1 ou 2 pièces": "appartement_1_2_pieces",
    "indicateur de loyer appartement de 3 pièces ou plus": "appartement_3_pieces_plus",
    "indicateurs de loyer appartement de 3 pièces et plus": "appartement_3_pieces_plus",
    "indicateurs de loyer maison": "maison",
}

COLONNES_ATTENDUES = {"INSEE_C", "loypredm2", "lwr.IPm2", "upr.IPm2", "TYPPRED", "nbobs_com"}


def discover_dataset(session: requests.Session) -> dict:
    """Retourne le jeu « Carte des loyers » du millésime le plus récent."""
    resp = session.get(RECHERCHE_URL, params={"q": REQUETE, "page_size": 10}, timeout=60)
    resp.raise_for_status()

    candidats = []
    for jeu in resp.json().get("data", []):
        titre = jeu.get("title", "")
        if "carte des loyers" not in titre.lower():
            continue
        # Le millésime figure en fin de titre : « … par commune en 2025 ».
        annees = [int(m) for m in titre.split() if m.isdigit() and len(m) == 4]
        if annees:
            candidats.append((max(annees), jeu))

    if not candidats:
        raise RuntimeError(
            "Aucun jeu « Carte des loyers » trouvé. L'intitulé a peut-être changé."
        )

    candidats.sort(key=lambda c: c[0], reverse=True)
    annee, jeu = candidats[0]
    logger.info("Millésime retenu : %d — %s", annee, jeu["title"][:70])
    jeu["_annee"] = annee
    return jeu


def nombre(serie: pd.Series) -> pd.Series:
    """Convertit une colonne numérique à virgule décimale.

    Le fichier est lu en chaînes pour préserver les codes INSEE à zéro initial ;
    `decimal=","` reste alors sans effet, et les virgules doivent être
    remplacées à la main.
    """
    return pd.to_numeric(
        serie.astype(str).str.replace(",", ".", regex=False), errors="coerce",
    )


def lire_ressource(session: requests.Session, url: str) -> pd.DataFrame:
    brut = session.get(url, timeout=180).content
    df = pd.read_csv(io.BytesIO(brut), sep=";", dtype=str, encoding=ENCODAGE)

    manquantes = COLONNES_ATTENDUES - set(df.columns)
    if manquantes:
        raise RuntimeError(
            f"Colonnes absentes du fichier loyers : {sorted(manquantes)}. "
            f"Colonnes présentes : {list(df.columns)}"
        )
    return df


def run_ingestion(departement: str) -> pd.DataFrame:
    session = requests.Session()
    jeu = discover_dataset(session)

    morceaux = []
    for res in jeu.get("resources", []):
        if res.get("format") != "csv":
            continue
        categorie = CATEGORIES.get((res.get("title") or "").strip().lower())
        if not categorie:
            continue

        df = lire_ressource(session, res["url"])
        df = df[df["INSEE_C"].astype(str).str[:2] == departement]

        morceaux.append(pd.DataFrame({
            "code_insee": df["INSEE_C"].astype(str),
            "categorie": categorie,
            "loyer_m2": nombre(df["loypredm2"]).round(2),
            "borne_basse": nombre(df["lwr.IPm2"]).round(2),
            "borne_haute": nombre(df["upr.IPm2"]).round(2),
            # « commune » : appuyé sur des annonces observées sur place.
            # « maille » : extrapolé depuis une zone plus large.
            "estimation_locale": df["TYPPRED"].astype(str).str.strip() == "commune",
            "nb_annonces": nombre(df["nbobs_com"]).fillna(0).astype(int),
            "millesime": str(jeu["_annee"]),
        }))
        logger.info("  %-28s %d communes", categorie, len(df))

    if not morceaux:
        raise RuntimeError(
            "Aucune ressource exploitable. Les intitulés du jeu ont peut-être changé : "
            f"{[r.get('title') for r in jeu.get('resources', []) if r.get('format') == 'csv']}"
        )

    return pd.concat(morceaux, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des loyers d'annonce")
    parser.add_argument("--departement", type=str, required=True)
    args = parser.parse_args()

    with timed_operation(logger, f"Ingestion loyers {args.departement}"):
        df = run_ingestion(args.departement)
        if df.empty:
            raise RuntimeError(f"Aucune donnée pour le département {args.departement}")

        write_parquet(df, PARQUET_DIR / "loyers.parquet")

        locales = df[df["estimation_locale"]]
        logger.info("=== RAPPORT LOYERS ===")
        logger.info("  Millésime            : %s", df["millesime"].iloc[0])
        logger.info("  Lignes               : %d", len(df))
        logger.info("  Communes             : %d", df["code_insee"].nunique())
        logger.info(
            "  Estimations locales  : %d (%.0f %%) — le reste est extrapolé "
            "depuis une zone plus large",
            len(locales), 100 * len(locales) / max(len(df), 1),
        )
        for cat, groupe in df.groupby("categorie"):
            logger.info("    %-28s médiane %.2f €/m²", cat, groupe["loyer_m2"].median())


if __name__ == "__main__":
    main()
