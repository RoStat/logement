"""Lot 8 — Rapprochement DVF ↔ DPE par adresse.

Associe chaque vente à un diagnostic énergétique, ce qui permet de croiser prix
et étiquette. Aucun identifiant commun n'existe entre les deux jeux : DVF ne
porte pas d'identifiant BAN. Le rapprochement se fait donc sur l'adresse
normalisée, puis se vérifie par la distance et la surface.

Trois garde-fous, parce qu'un faux appariement produit un prix au m² attribué à
la mauvaise étiquette, c'est-à-dire une donnée fausse plutôt qu'absente :

1. clé stricte — commune, numéro et voie normalisée, type de voie compris ;
2. clé souple — sans le type de voie, pour les cas où les deux sources ne
   s'accordent pas entre « rue » et « cours ». Réservée aux ventes non
   appariées, et systématiquement confirmée par la distance ;
3. vérification — distance inférieure au seuil, et surface habitable proche de
   la surface bâtie vendue.

Usage :
    python -m src.transform.rapprochement --departement 69
"""

import argparse
import re
import unicodedata

import numpy as np
import pandas as pd

from src.common.config import PARQUET_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("transform.rapprochement")

# Distance maximale entre le point DVF et le point DPE, en mètres. Le géocodage
# BAN pointe le centre de la voie quand le numéro est inconnu : au-delà de cette
# marge, on ne parle plus du même bâtiment.
DISTANCE_MAX_M = 150

# Écart relatif toléré entre surface vendue et surface du diagnostic.
ECART_SURFACE_MAX = 0.20

# Un diagnostic mal géocodé ne peut pas servir de preuve de localisation.
SCORE_BAN_MIN = 0.4

TYPES_VOIE = {
    "AV", "AVE", "AVENUE", "BD", "BLD", "BOULEVARD", "RUE", "CHE", "CHEMIN",
    "IMP", "IMPASSE", "PL", "PLACE", "RTE", "ROUTE", "ALL", "ALLEE", "ALLEES",
    "SQ", "SQUARE", "COURS", "QUAI", "MONTEE", "PASSAGE", "VOIE", "RESIDENCE",
    "LOTISSEMENT", "CITE", "CLOS", "PARC", "PROMENADE", "RAMPE", "TRABOULE",
}

ABREVIATIONS = {
    "AV": "AVENUE", "AVE": "AVENUE", "BD": "BOULEVARD", "BLD": "BOULEVARD",
    "CH": "CHEMIN", "CHE": "CHEMIN", "IMP": "IMPASSE", "PL": "PLACE", "RTE": "ROUTE",
    "ALL": "ALLEE", "SQ": "SQUARE", "ST": "SAINT", "STE": "SAINTE",
    "GAL": "GENERAL", "GEN": "GENERAL", "MAL": "MARECHAL", "DR": "DOCTEUR",
    "PR": "PROFESSEUR", "PDT": "PRESIDENT",
}

ARTICLES = {"DE", "DU", "DES", "LA", "LE", "LES", "D", "L", "AU", "AUX", "ET"}


def normaliser_voie(nom: str) -> str:
    """Normalise un nom de voie : majuscules, sans accent, abréviations résolues.

    Les articles sont retirés : DVF et l'ADEME ne les orthographient pas
    toujours pareil, et ils ne distinguent jamais deux voies d'une même commune.
    """
    if not isinstance(nom, str):
        return ""
    texte = unicodedata.normalize("NFD", nom.upper())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    texte = re.sub(r"[^A-Z0-9 ]+", " ", texte)

    mots = [ABREVIATIONS.get(m, m) for m in texte.split()]
    mots = [m for m in mots if m not in ARTICLES]
    return " ".join(mots)


def _colonnes_voie(serie: pd.Series) -> pd.DataFrame:
    """Applique separer_type_voie en préservant les colonnes sur une série vide."""
    if serie.empty:
        return pd.DataFrame({"type_voie": [], "voie_seule": []}, index=serie.index)
    return pd.DataFrame(
        serie.map(separer_type_voie).tolist(),
        index=serie.index,
        columns=["type_voie", "voie_seule"],
    )


def separer_type_voie(voie: str) -> tuple[str, str]:
    """Sépare le type de voie du libellé. « AVENUE JEAN JAURES » → (AVENUE, JEAN JAURES)."""
    mots = voie.split()
    if mots and mots[0] in TYPES_VOIE:
        return mots[0], " ".join(mots[1:])
    return "", voie


def distance_m(lat1, lon1, lat2, lon2):
    """Distance approchée en mètres, suffisante à l'échelle d'une commune."""
    lat_moy = np.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * np.cos(lat_moy) * 111_320
    dy = (lat2 - lat1) * 111_320
    return np.sqrt(dx**2 + dy**2)


def preparer_dvf(dvf: pd.DataFrame) -> pd.DataFrame:
    d = dvf.copy()
    d["voie_norm"] = d["adresse_nom_voie"].map(normaliser_voie)
    d[["type_voie", "voie_seule"]] = _colonnes_voie(d["voie_norm"])
    d["numero"] = pd.to_numeric(d["adresse_numero"], errors="coerce")
    d["id_voie"] = d["code_commune"].astype(str) + d["adresse_code_voie"].astype(str)
    d["cle_stricte"] = (
        d["code_commune"].astype(str) + "|" + d["numero"].astype("Int64").astype(str)
        + "|" + d["voie_norm"]
    )
    d["cle_souple"] = (
        d["code_commune"].astype(str) + "|" + d["numero"].astype("Int64").astype(str)
        + "|" + d["voie_seule"]
    )
    return d


def preparer_dpe(dpe: pd.DataFrame) -> pd.DataFrame:
    d = dpe[dpe["score_ban"].fillna(0) >= SCORE_BAN_MIN].copy()

    # Aucun diagnostic ne passe le seuil de géocodage : on rend une table vide
    # au bon schéma. Sans cela, `split(expand=True)` ne crée aucune colonne et
    # la concaténation des clés échoue sur des types incompatibles.
    if d.empty:
        return pd.DataFrame(columns=[
            "numero_dpe", "classe_dpe", "classe_ges", "conso_energie",
            "surface_habitable", "dpe_lat", "dpe_lon", "score_ban",
            "cle_stricte", "cle_souple",
        ])

    coords = d["geopoint"].astype(str).str.split(",", n=1, expand=True)
    d["dpe_lat"] = pd.to_numeric(coords[0], errors="coerce")
    d["dpe_lon"] = pd.to_numeric(coords[1], errors="coerce")
    d = d[d["dpe_lat"].notna() & d["dpe_lon"].notna()]

    d["voie_norm"] = d["nom_rue"].map(normaliser_voie)
    d[["type_voie", "voie_seule"]] = _colonnes_voie(d["voie_norm"])
    d["numero"] = pd.to_numeric(d["numero_voie"], errors="coerce")
    d["cle_stricte"] = (
        d["code_insee"].astype(str) + "|" + d["numero"].astype("Int64").astype(str)
        + "|" + d["voie_norm"]
    )
    d["cle_souple"] = (
        d["code_insee"].astype(str) + "|" + d["numero"].astype("Int64").astype(str)
        + "|" + d["voie_seule"]
    )
    return d


def apparier(dvf: pd.DataFrame, dpe: pd.DataFrame, cle: str) -> pd.DataFrame:
    """Joint sur une clé d'adresse, puis retient le meilleur candidat par vente."""
    colonnes_dpe = [
        cle, "numero_dpe", "classe_dpe", "classe_ges", "conso_energie",
        "surface_habitable", "dpe_lat", "dpe_lon", "score_ban",
    ]
    joint = dvf.merge(dpe[colonnes_dpe], on=cle, how="inner", suffixes=("", "_dpe"))
    if joint.empty:
        return joint

    joint["distance_m"] = distance_m(
        joint["latitude"].astype(float), joint["longitude"].astype(float),
        joint["dpe_lat"], joint["dpe_lon"],
    )
    joint["ecart_surface"] = (
        (joint["surface_habitable"] - joint["surface_reelle_bati"]).abs()
        / joint["surface_reelle_bati"].replace(0, np.nan)
    )

    retenus = joint[
        (joint["distance_m"] <= DISTANCE_MAX_M)
        & (joint["ecart_surface"] <= ECART_SURFACE_MAX)
    ].copy()
    if retenus.empty:
        return retenus

    # Un immeuble porte plusieurs diagnostics : on garde celui dont la surface
    # colle le mieux au bien vendu, la distance départageant les ex aequo.
    retenus = retenus.sort_values(["ecart_surface", "distance_m"])
    return retenus.drop_duplicates(subset="id_mutation", keep="first")


def rapprocher(dvf_brut: pd.DataFrame, dpe_brut: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    dvf = preparer_dvf(dvf_brut)
    dpe = preparer_dpe(dpe_brut)

    eligibles = dvf[dvf["latitude"].notna() & dvf["numero"].notna()]

    stricts = apparier(eligibles, dpe, "cle_stricte")
    restants = eligibles[~eligibles["id_mutation"].isin(stricts.get("id_mutation", []))]
    souples = apparier(restants, dpe, "cle_souple")

    apparies = pd.concat([stricts, souples], ignore_index=True)
    apparies["methode"] = ["stricte"] * len(stricts) + ["souple"] * len(souples)

    stats = {
        "ventes": len(dvf_brut),
        "ventes_geolocalisees": len(eligibles),
        "dpe_exploitables": len(dpe),
        "apparies_cle_stricte": len(stricts),
        "apparies_cle_souple": len(souples),
        "apparies_total": len(apparies),
        "taux_appariement_pct": round(100 * len(apparies) / max(len(eligibles), 1), 1),
    }
    if len(apparies):
        stats["distance_mediane_m"] = round(float(apparies["distance_m"].median()), 1)
        stats["ecart_surface_median_pct"] = round(
            float(apparies["ecart_surface"].median()) * 100, 1,
        )
    return apparies, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Rapprochement DVF ↔ DPE")
    parser.add_argument("--departement", type=str, required=True)
    args = parser.parse_args()

    with timed_operation(logger, "Rapprochement DVF/DPE"):
        dvf = pd.concat(
            [pd.read_parquet(f) for f in sorted((PARQUET_DIR / "dvf").glob("*.parquet"))],
            ignore_index=True,
        )
        dpe = pd.concat(
            [pd.read_parquet(f) for f in sorted((PARQUET_DIR / "dpe").glob("*.parquet"))],
            ignore_index=True,
        )

        dvf = dvf[dvf["code_departement"].astype(str) == args.departement]
        dpe = dpe[dpe["code_departement"].astype(str) == args.departement]

        apparies, stats = rapprocher(dvf, dpe)

        colonnes = [
            "id_mutation", "code_commune", "type_local", "date_mutation",
            "valeur_fonciere", "surface_reelle_bati", "id_voie",
            "numero_dpe", "classe_dpe", "classe_ges", "conso_energie",
            "distance_m", "ecart_surface", "methode",
        ]
        write_parquet(apparies[colonnes], PARQUET_DIR / "dvf_dpe.parquet")

        logger.info("=== RAPPORT RAPPROCHEMENT ===")
        for cle, valeur in stats.items():
            logger.info("  %-26s %s", cle, valeur)

        if len(apparies):
            logger.info("  Répartition des étiquettes appariées :")
            for classe, n in apparies["classe_dpe"].value_counts().sort_index().items():
                logger.info("    %s : %d", classe, n)


if __name__ == "__main__":
    main()
