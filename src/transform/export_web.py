"""Lot 7 — Export des données destinées au site.

Produit le fichier JSON embarqué dans la page : agrégats communaux, contours du
département et bornes des échelles de couleur. Le millésime retenu est le
dernier disponible pour chaque commune, et non une année figée dans le code.

Usage :
    python -m src.transform.export_web --departement 69
"""

import argparse
import json
import sqlite3

import pandas as pd

from src.common.config import DB_PATH, PARQUET_DIR
from src.common.logging import get_logger, timed_operation

logger = get_logger("transform.export_web")

# Rang des étiquettes DPE, servant à calculer une moyenne communale. A vaut 1 et
# G vaut 7 : plus la moyenne est élevée, plus le parc est énergivore.
RANG_DPE = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7}


def dernier_millesime(df: pd.DataFrame, cle: str = "code_insee") -> pd.DataFrame:
    """Ne conserve, pour chaque commune, que la ligne de l'année la plus récente."""
    return df.sort_values("annee").groupby(cle, as_index=False).last()


def construire(departement: str) -> dict:
    con = sqlite3.connect(str(DB_PATH))

    communes = pd.read_sql("SELECT code_insee, nom, slug FROM communes", con)
    eligibles = set(pd.read_sql("SELECT code_insee FROM communes_eligible", con)["code_insee"])

    prix = dernier_millesime(pd.read_sql(
        "SELECT code_insee, annee, nb_ventes, prix_m2_median FROM agg_commune_prix", con))

    immo = pd.read_sql(
        "SELECT code_insee, annee, type_local, nb_ventes, prix_m2_median, "
        "prix_median, surface_mediane FROM agg_commune_immo", con)

    dpe = pd.read_sql(
        "SELECT code_insee, classe_dpe, nb_logements, part_pct FROM agg_commune_dpe", con)

    fibre = pd.read_parquet(PARQUET_DIR / "fibre.parquet")

    # Moyenne pondérée du rang DPE : un parc noté C en moyenne vaut 3.
    dpe["rang"] = dpe["classe_dpe"].map(RANG_DPE)
    moyennes = dpe.dropna(subset=["rang"]).groupby("code_insee").apply(
        lambda g: round((g["rang"] * g["nb_logements"]).sum() / g["nb_logements"].sum(), 2),
        include_groups=False,
    )

    sortie = {}
    for _, c in communes.iterrows():
        code = c["code_insee"]
        if code not in eligibles:
            continue

        entree = {"n": c["nom"], "s": c["slug"], "v": {}}

        ligne_prix = prix[prix["code_insee"] == code]
        if not ligne_prix.empty:
            r = ligne_prix.iloc[0]
            entree["p"] = int(r["prix_m2_median"])
            entree["a"] = int(r["annee"])
            entree["nv"] = int(r["nb_ventes"])

        recent = dernier_millesime(immo[immo["code_insee"] == code], "type_local")
        for _, r in recent.iterrows():
            cle = "app" if r["type_local"] == "Appartement" else "mai"
            entree["v"][cle] = [
                int(r["nb_ventes"]), int(r["prix_m2_median"]),
                int(r["prix_median"]), int(r["surface_mediane"]), int(r["annee"]),
            ]

        part = dpe[dpe["code_insee"] == code]
        if not part.empty:
            entree["d"] = {r["classe_dpe"]: r["part_pct"] for _, r in part.iterrows()}
        if code in moyennes.index:
            entree["dm"] = float(moyennes[code])

        ligne_fibre = fibre[fibre["code_insee"] == code]
        if not ligne_fibre.empty and pd.notna(ligne_fibre.iloc[0]["taux_fibre_pct"]):
            entree["f"] = float(ligne_fibre.iloc[0]["taux_fibre_pct"])

        sortie[code] = entree

    con.close()

    chemin_contours = PARQUET_DIR / f"contours_{departement}.json"
    contours = json.loads(chemin_contours.read_text(encoding="utf-8"))
    millesime_fibre = fibre["millesime"].iloc[0] if len(fibre) else None

    return {"communes": sortie, "contours": contours, "millesimeFibre": millesime_fibre}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export des données du site")
    parser.add_argument("--departement", type=str, required=True)
    args = parser.parse_args()

    with timed_operation(logger, "Export web"):
        donnees = construire(args.departement)
        chemin = PARQUET_DIR / f"web_{args.departement}.json"
        chemin.write_text(json.dumps(donnees, ensure_ascii=True, separators=(",", ":")),
                          encoding="utf-8")

        communes = donnees["communes"]
        annees = {c["a"] for c in communes.values() if "a" in c}
        logger.info("=== RAPPORT EXPORT ===")
        logger.info("  Communes exportées : %d", len(communes))
        logger.info("  Millésimes prix    : %s", sorted(annees))
        logger.info("  Avec DPE moyen     : %d", sum("dm" in c for c in communes.values()))
        logger.info("  Avec taux fibre    : %d", sum("f" in c for c in communes.values()))
        logger.info("  Contours           : %d communes", len(donnees["contours"]["communes"]))
        logger.info("  Fichier            : %s (%.0f Ko)", chemin, chemin.stat().st_size / 1024)


if __name__ == "__main__":
    main()
