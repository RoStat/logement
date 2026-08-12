"""Lot 3 — Ingestion du référentiel géographique (COG INSEE + BAN voies).

Construit les tables `communes` et `voies` conformément au modèle §7.1.

Usage :
    python -m src.ingest.geo
    python -m src.ingest.geo --departement 69
"""

import argparse
import io
import re
import unicodedata
import zipfile

import pandas as pd
import requests

from src.common.config import DATA_DIR, GEO_RAW_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.geo")

COG_DATASET_URL = "https://www.data.gouv.fr/api/1/datasets/code-officiel-geographique-cog/"

COG_COMMUNES_EXPECTED_COLUMNS = {
    "TYPECOM", "COM", "REG", "DEP", "LIBELLE", "NCCENR", "COMPARENT",
}

REGIONS = {
    "01": "Guadeloupe",
    "02": "Martinique",
    "03": "Guyane",
    "04": "La Réunion",
    "06": "Mayotte",
    "11": "Île-de-France",
    "24": "Centre-Val de Loire",
    "27": "Bourgogne-Franche-Comté",
    "28": "Normandie",
    "32": "Hauts-de-France",
    "44": "Grand Est",
    "52": "Pays de la Loire",
    "53": "Bretagne",
    "75": "Nouvelle-Aquitaine",
    "76": "Occitanie",
    "84": "Auvergne-Rhône-Alpes",
    "93": "Provence-Alpes-Côte d'Azur",
    "94": "Corse",
}

DEPARTEMENTS = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron",
    "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "21": "Côte-d'Or",
    "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs",
    "26": "Drôme", "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère",
    "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "30": "Gard", "31": "Haute-Garonne", "32": "Gers", "33": "Gironde",
    "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire",
    "38": "Isère", "39": "Jura", "40": "Landes", "41": "Loir-et-Cher",
    "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique", "45": "Loiret",
    "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère", "49": "Maine-et-Loire",
    "50": "Manche", "51": "Marne", "52": "Haute-Marne", "53": "Mayenne",
    "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan", "57": "Moselle",
    "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne",
    "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin",
    "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône",
    "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie", "74": "Haute-Savoie",
    "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne", "78": "Yvelines",
    "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne",
    "83": "Var", "84": "Vaucluse", "85": "Vendée", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise",
    "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}

ARRONDISSEMENTS_PARENT = {
    "75056": list(range(75101, 75121)),
    "69123": list(range(69381, 69390)),
    "13055": list(range(13201, 13217)),
}


def slugify(text: str) -> str:
    """Convertit un nom en slug URL : minuscules, sans accents, tirets.

    Exemples :
        Saint-Étienne → saint-etienne
        L'Haÿ-les-Roses → l-hay-les-roses
    """
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"['']+", "-", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def deduplicate_slugs(slugs: pd.Series) -> pd.Series:
    """Ajoute un suffixe numérique aux slugs en double."""
    seen: dict[str, int] = {}
    result = []
    for s in slugs:
        if s in seen:
            seen[s] += 1
            result.append(f"{s}-{seen[s]}")
        else:
            seen[s] = 0
            result.append(s)
    return pd.Series(result, index=slugs.index)


def discover_cog_url(session: requests.Session) -> str:
    """Interroge l'API data.gouv.fr pour trouver l'URL du fichier communes du COG le plus récent."""
    logger.info("Recherche du fichier communes COG sur data.gouv.fr…")
    resp = session.get(COG_DATASET_URL, timeout=30)
    resp.raise_for_status()
    dataset = resp.json()

    candidates = []
    for resource in dataset.get("resources", []):
        title = (resource.get("title") or "").lower()
        url = resource.get("url", "")
        if "commune" in title and url.endswith((".csv", ".csv.gz", ".zip")):
            candidates.append((title, url))

    if not candidates:
        raise RuntimeError(
            f"Aucun fichier communes trouvé dans le dataset COG. "
            f"Ressources disponibles : {[r.get('title') for r in dataset.get('resources', [])]}"
        )

    for title, url in candidates:
        if "commune" in title and ("2024" in title or "2025" in title or "2026" in title):
            logger.info("Fichier COG retenu : %s → %s", title, url)
            return url

    title, url = candidates[0]
    logger.info("Fichier COG retenu (premier candidat) : %s → %s", title, url)
    return url


def download_cog(session: requests.Session) -> pd.DataFrame:
    """Télécharge et parse le fichier communes du COG."""
    url = discover_cog_url(session)

    with timed_operation(logger, f"Téléchargement COG depuis {url}"):
        resp = session.get(url, timeout=120)
        resp.raise_for_status()
        content = resp.content

    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                raise RuntimeError(f"Pas de CSV dans le ZIP COG : {zf.namelist()}")
            csv_name = csv_names[0]
            logger.info("Extraction de %s depuis le ZIP", csv_name)
            with zf.open(csv_name) as f:
                df = pd.read_csv(f, dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(content), dtype=str)

    missing = COG_COMMUNES_EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Colonnes manquantes dans le COG : {missing}. "
            f"Colonnes présentes : {list(df.columns)}"
        )

    logger.info("COG chargé : %d lignes, colonnes : %s", len(df), list(df.columns))
    return df


def build_communes(cog_df: pd.DataFrame) -> pd.DataFrame:
    """Construit la table communes à partir du COG."""
    communes = cog_df[cog_df["TYPECOM"] == "COM"].copy()
    arrondissements = cog_df[cog_df["TYPECOM"] == "ARM"].copy()

    logger.info(
        "COG filtré : %d communes (COM), %d arrondissements (ARM)",
        len(communes), len(arrondissements),
    )

    all_rows = pd.concat([communes, arrondissements], ignore_index=True)

    dep_col = "DEP"
    reg_col = "REG"

    result = pd.DataFrame({
        "code_insee": all_rows["COM"],
        "nom": all_rows["LIBELLE"],
        "code_departement": all_rows[dep_col],
        "code_region": all_rows[reg_col],
    })

    result["nom_departement"] = result["code_departement"].map(DEPARTEMENTS).fillna("")
    result["nom_region"] = result["code_region"].map(REGIONS).fillna("")

    result["slug"] = deduplicate_slugs(result["nom"].apply(slugify))

    result["codes_postaux"] = ""
    result["population"] = None
    result["latitude"] = None
    result["longitude"] = None

    result = result[[
        "code_insee", "nom", "slug", "codes_postaux",
        "code_departement", "nom_departement",
        "code_region", "nom_region",
        "population", "latitude", "longitude",
    ]]

    n_dup = result["slug"].duplicated().sum()
    if n_dup > 0:
        raise RuntimeError(
            f"{n_dup} slugs en double — bogue dans deduplicate_slugs"
        )

    logger.info("Table communes : %d lignes", len(result))
    return result


def enrich_with_ban_communes(communes_df: pd.DataFrame, session: requests.Session) -> pd.DataFrame:
    """Enrichit les communes avec les coordonnées et codes postaux depuis l'API BAN."""
    logger.info("Enrichissement des communes via l'API de géocodage BAN (par lots)…")

    ban_url = "https://api-adresse.data.gouv.fr/search/csv/"

    csv_buf = io.StringIO()
    subset = communes_df[["code_insee", "nom"]].copy()
    subset.columns = ["code_insee", "commune"]
    subset.to_csv(csv_buf, index=False)

    batch_size = 5000
    enriched_parts = []
    total = len(subset)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = subset.iloc[start:end]

        batch_buf = io.StringIO()
        batch.to_csv(batch_buf, index=False)

        try:
            resp = session.post(
                ban_url,
                files={"data": ("communes.csv", batch_buf.getvalue(), "text/csv")},
                data={"columns": "commune", "citycode": "code_insee"},
                timeout=120,
            )
            resp.raise_for_status()
            result = pd.read_csv(io.StringIO(resp.text), dtype=str)
            enriched_parts.append(result)
            logger.info("  BAN batch %d–%d/%d : %d résultats", start, end, total, len(result))
        except Exception as e:
            logger.warning("  BAN batch %d–%d échoué : %s — coordonnées manquantes", start, end, e)

    if not enriched_parts:
        logger.warning("Aucun enrichissement BAN réussi — coordonnées non disponibles")
        return communes_df

    enriched = pd.concat(enriched_parts, ignore_index=True)

    if "result_citycode" in enriched.columns and "latitude" in enriched.columns:
        coords = enriched.groupby("code_insee").first()[["latitude", "longitude"]].reset_index()
        coords["latitude"] = pd.to_numeric(coords["latitude"], errors="coerce")
        coords["longitude"] = pd.to_numeric(coords["longitude"], errors="coerce")
        communes_df = communes_df.merge(
            coords.rename(columns={"latitude": "lat_ban", "longitude": "lon_ban"}),
            on="code_insee", how="left",
        )
        communes_df["latitude"] = communes_df["lat_ban"].combine_first(communes_df["latitude"])
        communes_df["longitude"] = communes_df["lon_ban"].combine_first(communes_df["longitude"])
        communes_df = communes_df.drop(columns=["lat_ban", "lon_ban"])

    return communes_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion référentiel géographique")
    parser.add_argument(
        "--departement", type=str, default=None, help="Département (ex: 69)",
    )
    parser.add_argument("--skip-ban", action="store_true", help="Ne pas enrichir via la BAN")
    args = parser.parse_args()

    GEO_RAW_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    with timed_operation(logger, "Ingestion référentiel géographique"):
        cog_df = download_cog(session)
        communes = build_communes(cog_df)

        if args.departement:
            communes = communes[communes["code_departement"] == args.departement]
            logger.info("Filtre département %s : %d communes", args.departement, len(communes))

        if not args.skip_ban:
            try:
                communes = enrich_with_ban_communes(communes, session)
            except Exception as e:
                logger.warning("Enrichissement BAN échoué : %s — on continue sans coordonnées", e)

        out_path = DATA_DIR / "parquet" / "communes.parquet"
        write_parquet(communes, out_path)
        logger.info("Communes écrites : %s (%d lignes)", out_path, len(communes))

        n_total = len(communes)
        n_slugs_uniques = communes["slug"].nunique()
        n_with_coords = communes["latitude"].notna().sum()

        logger.info("=== RAPPORT ===")
        logger.info("  Communes totales : %d", n_total)
        logger.info("  Slugs uniques    : %d", n_slugs_uniques)
        logger.info("  Avec coordonnées : %d", n_with_coords)

        if n_slugs_uniques != n_total:
            raise RuntimeError(
                f"ERREUR : {n_total - n_slugs_uniques} slugs en double !"
            )


if __name__ == "__main__":
    main()
