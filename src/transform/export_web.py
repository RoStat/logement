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

# En deçà, une médiane communale par étiquette ne veut rien dire.
OBSERVATIONS_MIN = 8

# Nombre d'étiquettes distinctes requis pour qu'une comparaison ait un sens.
CLASSES_MIN = 3


def effet_dpe_departemental(croisement: pd.DataFrame) -> dict:
    """Écart de prix par étiquette, mesuré à commune constante.

    Agrégé sur tout le département, le résultat est trompeur : les logements
    classés G sont massivement des immeubles anciens d'hypercentre, et
    ressortent au-dessus des D. La localisation écrase l'effet énergétique, il
    faut donc comparer chaque étiquette au sein de sa propre commune.
    """
    d = croisement[croisement["nb_observations"] >= 5].copy()
    reference = d[d["classe_dpe"] == "D"].set_index("code_insee")["prix_m2_median"]
    d["reference"] = d["code_insee"].map(reference)
    d = d[d["reference"].notna() & (d["reference"] > 0)]
    if d.empty:
        return {}

    d["ratio"] = d["prix_m2_median"] / d["reference"]
    effet = {}
    for classe, groupe in d.groupby("classe_dpe"):
        poids = groupe["nb_observations"].sum()
        moyenne = (groupe["ratio"] * groupe["nb_observations"]).sum() / poids
        effet[classe] = [round((moyenne - 1) * 100, 1), int(poids)]
    return effet


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
        "prix_median, surface_mediane, prix_m2_p25, prix_m2_p75 "
        "FROM agg_commune_immo", con)

    dpe = pd.read_sql(
        "SELECT code_insee, classe_dpe, nb_logements, part_pct FROM agg_commune_dpe", con)

    fibre = pd.read_parquet(PARQUET_DIR / "fibre.parquet")
    loyers = pd.read_parquet(PARQUET_DIR / "loyers.parquet")

    # Le COG ne porte pas les codes postaux. Les diagnostics, eux, en portent un
    # par logement : on retient le plus fréquent de chaque commune, seul utile
    # pour construire les liens vers les portails d'annonces.
    dpe_cp = pd.read_parquet(PARQUET_DIR / "dpe" / f"dpe_dep{departement}.parquet",
                             columns=["code_insee", "code_postal"])
    codes_postaux = (
        dpe_cp.dropna().groupby("code_insee")["code_postal"]
        .agg(lambda s: s.mode().iloc[0] if len(s.mode()) else None)
    )

    croisement = pd.read_sql(
        "SELECT code_insee, type_local, classe_dpe, nb_observations, prix_m2_median "
        "FROM agg_commune_croisement", con)

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
        if code in codes_postaux.index and codes_postaux[code]:
            entree["cp"] = str(codes_postaux[code])

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
            # Écart interquartile du prix au m² : sert de fourchette de budget,
            # bornée par des ventes réelles plutôt que par une marge arbitraire.
            if pd.notna(r["prix_m2_p25"]) and pd.notna(r["prix_m2_p75"]):
                entree.setdefault("q", {})[cle] = [
                    int(r["prix_m2_p25"]), int(r["prix_m2_p75"]),
                ]

        part = dpe[dpe["code_insee"] == code]
        if not part.empty:
            entree["d"] = {r["classe_dpe"]: r["part_pct"] for _, r in part.iterrows()}
        if code in moyennes.index:
            entree["dm"] = float(moyennes[code])

        # Prix par étiquette dans la commune, appartements seulement : mêler
        # maisons et appartements comparerait des marchés différents.
        cr = croisement[
            (croisement["code_insee"] == code)
            & (croisement["type_local"] == "Appartement")
            & (croisement["nb_observations"] >= OBSERVATIONS_MIN)
        ]
        if len(cr) >= CLASSES_MIN:
            entree["cr"] = {
                r["classe_dpe"]: [int(r["prix_m2_median"]), int(r["nb_observations"])]
                for _, r in cr.iterrows()
            }

        # Loyers : on ne retient que les catégories directement comparables aux
        # agrégats de vente, appartement et maison.
        loy = loyers[
            (loyers["code_insee"] == code)
            & (loyers["categorie"].isin(["appartement", "maison"]))
        ]
        for _, r in loy.iterrows():
            cle = "app" if r["categorie"] == "appartement" else "mai"
            entree.setdefault("l", {})[cle] = [
                float(r["loyer_m2"]), float(r["borne_basse"]), float(r["borne_haute"]),
                # Faux : estimation extrapolée depuis une zone plus large, et non
                # mesurée sur des annonces de la commune.
                bool(r["estimation_locale"]),
            ]
        if not loy.empty:
            entree["lm"] = str(loy["millesime"].iloc[0])

        ligne_fibre = fibre[fibre["code_insee"] == code]
        if not ligne_fibre.empty and pd.notna(ligne_fibre.iloc[0]["taux_fibre_pct"]):
            entree["f"] = float(ligne_fibre.iloc[0]["taux_fibre_pct"])

        sortie[code] = entree

    con.close()

    chemin_contours = PARQUET_DIR / f"contours_{departement}.json"
    contours = json.loads(chemin_contours.read_text(encoding="utf-8"))
    millesime_fibre = fibre["millesime"].iloc[0] if len(fibre) else None

    return {
        "communes": sortie,
        "contours": contours,
        "millesimeFibre": millesime_fibre,
        "effetDpe": effet_dpe_departemental(croisement[croisement["type_local"] == "Appartement"]),
    }


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
        logger.info("  Avec croisement DPE: %d", sum("cr" in c for c in communes.values()))
        logger.info("  Avec code postal   : %d", sum("cp" in c for c in communes.values()))
        avec_loyer = [c for c in communes.values() if "l" in c]
        locales = [c for c in avec_loyer if any(v[3] for v in c["l"].values())]
        logger.info("  Avec loyer         : %d", len(avec_loyer))
        logger.info(
            "    dont estimation locale : %d — pour les autres, le loyer est "
            "extrapolé depuis une zone plus large", len(locales),
        )
        logger.info("  Effet DPE départemental (écart au D, à commune constante) :")
        for classe in "ABCDEFG":
            if classe in donnees["effetDpe"]:
                ecart, n = donnees["effetDpe"][classe]
                logger.info("    %s : %+6.1f %%  (%d observations)", classe, ecart, n)
        logger.info("  Contours           : %d communes", len(donnees["contours"]["communes"]))
        logger.info("  Fichier            : %s (%.0f Ko)", chemin, chemin.stat().st_size / 1024)


if __name__ == "__main__":
    main()
