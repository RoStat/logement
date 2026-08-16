"""Lot 6 — Déploiement de la fibre optique (ARCEP).

L'ARCEP publie chaque trimestre l'état du déploiement FTTH commune par commune,
sous forme de shapefile. Le fichier porte à la fois les compteurs de locaux et
les contours administratifs : ce script en tire donc deux sorties, les données
fibre et les contours servant à la carte du département.

Les contours de l'ARCEP sont préférés à ceux de `geo.api.gouv.fr`, qui agrège
Lyon en une commune unique là où le parc immobilier et le prix au m² diffèrent
fortement d'un arrondissement à l'autre.

Usage :
    python -m src.ingest.fibre --departement 69
"""

import argparse
import json
import zipfile

import pandas as pd
import requests
import shapefile
from shapely.geometry import shape

from src.common.config import DATA_DIR, PARQUET_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.fibre")

ARCEP_DATASET_URL = (
    "https://www.data.gouv.fr/api/1/datasets/547d8d7ac751df405d090fcb/"
)

# Tolérance de simplification des contours, en mètres (projection Web Mercator).
# À l'échelle d'un département rendu sur un millier de pixels, un point vaut une
# centaine de mètres : en deçà, le détail supprimé est invisible.
TOLERANCE_METRES = 80

# Repère du tracé produit : les contours sont normalisés dans un carré de côté
# fixe, directement exploitable comme viewBox SVG.
COTE_VUE = 1000

CHAMPS_ATTENDUS = {"INSEE_COM", "INSEE_DEP", "NOM_COM", "Locaux", "ftth", "POPULATION"}


def discover_arcep_url(session: requests.Session) -> tuple[str, str]:
    """Trouve la ressource communale ARCEP la plus récente. Retourne (millésime, url)."""
    logger.info("Recherche de la ressource communale ARCEP…")
    resp = session.get(ARCEP_DATASET_URL, timeout=60)
    resp.raise_for_status()

    candidats = []
    for res in resp.json().get("resources", []):
        titre = (res.get("title") or "").strip()
        # Les ressources communales sont nommées « 2026T1-Commune ».
        if titre.lower().endswith("-commune") and res.get("format") == "zip":
            candidats.append((titre.split("-")[0], res["url"]))

    if not candidats:
        raise RuntimeError(
            "Aucune ressource communale trouvée dans le jeu ARCEP. "
            "Le nommage des ressources a peut-être changé."
        )

    candidats.sort(reverse=True)
    millesime, url = candidats[0]
    logger.info("Millésime retenu : %s → %s", millesime, url)
    return millesime, url


def telecharger(session: requests.Session, url: str) -> str:
    """Télécharge et décompresse l'archive. Retourne le préfixe du shapefile."""
    dossier = DATA_DIR / "raw" / "fibre"
    dossier.mkdir(parents=True, exist_ok=True)
    archive = dossier / "commune.zip"

    with timed_operation(logger, "Téléchargement ARCEP"):
        resp = session.get(url, timeout=600)
        resp.raise_for_status()
        archive.write_bytes(resp.content)
        logger.info("  %.1f Mo", len(resp.content) / (1024 * 1024))

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dossier)
        shp = [n for n in zf.namelist() if n.endswith(".shp")]

    if not shp:
        raise RuntimeError(f"Pas de shapefile dans l'archive : {zf.namelist()}")
    return str(dossier / shp[0][:-4])


def lire_departement(prefixe: str, departement: str) -> tuple[pd.DataFrame, list]:
    """Extrait les enregistrements et géométries d'un département."""
    lecteur = shapefile.Reader(prefixe)
    noms = [f[0] for f in lecteur.fields[1:]]

    manquants = CHAMPS_ATTENDUS - set(noms)
    if manquants:
        raise RuntimeError(
            f"Champs absents du shapefile ARCEP : {manquants}. "
            f"Champs présents : {noms}"
        )

    idx = {n: k for k, n in enumerate(noms)}
    lignes, geometries = [], []

    for sr in lecteur.iterShapeRecords():
        if sr.record[idx["INSEE_DEP"]] != departement:
            continue
        locaux = sr.record[idx["Locaux"]] or 0
        ftth = sr.record[idx["ftth"]] or 0
        lignes.append({
            "code_insee": sr.record[idx["INSEE_COM"]],
            "nom": sr.record[idx["NOM_COM"]],
            "locaux": int(locaux),
            "locaux_ftth": int(ftth),
            # `couv` du fichier source ne prend que trois valeurs (50, 80, 95) :
            # c'est un palier réglementaire, pas un taux. On le recalcule.
            "taux_fibre_pct": round(100 * ftth / locaux, 1) if locaux else None,
            "population": int(sr.record[idx["POPULATION"]] or 0),
        })
        geometries.append((sr.record[idx["INSEE_COM"]], shape(sr.shape.__geo_interface__)))

    return pd.DataFrame(lignes), geometries


def construire_contours(geometries: list) -> dict:
    """Simplifie et normalise les contours dans un repère de tracé carré."""
    simplifiees = [
        (code, geo.simplify(TOLERANCE_METRES, preserve_topology=True))
        for code, geo in geometries
    ]

    xs = [c for _, g in simplifiees for c in (g.bounds[0], g.bounds[2])]
    ys = [c for _, g in simplifiees for c in (g.bounds[1], g.bounds[3])]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    echelle = COTE_VUE / max(x1 - x0, y1 - y0)

    def projeter(x: float, y: float) -> list[int]:
        # L'axe des ordonnées est inversé : en SVG il croît vers le bas.
        return [round((x - x0) * echelle), round((y1 - y) * echelle)]

    def anneaux(geo) -> list[list[list[int]]]:
        polygones = [geo] if geo.geom_type == "Polygon" else list(geo.geoms)
        return [
            [projeter(x, y) for x, y in p.exterior.coords]
            for p in polygones
            if not p.is_empty
        ]

    return {
        "largeur": round((x1 - x0) * echelle),
        "hauteur": round((y1 - y0) * echelle),
        "communes": {code: anneaux(geo) for code, geo in simplifiees},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion du déploiement fibre (ARCEP)")
    parser.add_argument("--departement", type=str, required=True, help="Département (ex: 69)")
    args = parser.parse_args()

    with timed_operation(logger, f"Ingestion fibre {args.departement}"):
        session = requests.Session()
        millesime, url = discover_arcep_url(session)
        prefixe = telecharger(session, url)

        df, geometries = lire_departement(prefixe, args.departement)
        if df.empty:
            raise RuntimeError(f"Aucune commune trouvée pour le département {args.departement}")

        df["millesime"] = millesime
        write_parquet(df, PARQUET_DIR / "fibre.parquet")

        contours = construire_contours(geometries)
        chemin = PARQUET_DIR / f"contours_{args.departement}.json"
        chemin.write_text(json.dumps(contours, separators=(",", ":")), encoding="utf-8")

        couverts = df[df["taux_fibre_pct"].notna()]
        logger.info("=== RAPPORT FIBRE ===")
        logger.info("  Millésime          : %s", millesime)
        logger.info("  Communes           : %d", len(df))
        logger.info("  Locaux recensés    : %s", f"{df['locaux'].sum():,}".replace(",", " "))
        logger.info("  Locaux raccordables: %s", f"{df['locaux_ftth'].sum():,}".replace(",", " "))
        logger.info("  Taux départemental : %.1f %%",
                    100 * df["locaux_ftth"].sum() / max(df["locaux"].sum(), 1))
        logger.info("  Taux communal médian : %.1f %%", couverts["taux_fibre_pct"].median())
        logger.info("  Contours           : %s (%.0f Ko)", chemin, chemin.stat().st_size / 1024)


if __name__ == "__main__":
    main()
