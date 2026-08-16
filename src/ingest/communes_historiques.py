"""Lot 3bis — Table de rattachement des communes déléguées.

Les fusions de communes laissent dans le COG des lignes `COMD` (communes
déléguées) et `COMA` (communes associées), dépourvues de département et de
région : ce sont d'anciennes communes absorbées par une commune nouvelle.

DVF et DPE portent l'historique sous les anciens codes INSEE. Sans table de
rattachement, ces lignes n'ont plus de commune d'accueil : sur le seul
département 69, 580 ventes et 1 599 DPE se retrouvent orphelins, et le contrôle
qualité « toute commune a un département » échoue.

Ce script est délibérément séparé de `geo.py`, qui produit le référentiel des
communes actives et reste inchangé.

Usage :
    python -m src.ingest.communes_historiques
    python -m src.ingest.communes_historiques --departement 69
"""

import argparse

import pandas as pd
import requests

from src.common.config import DATA_DIR
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet
from src.ingest.geo import download_cog

logger = get_logger("ingest.communes_historiques")

# Types de communes absorbées, à rattacher à leur commune nouvelle.
TYPES_HISTORIQUES = ("COMD", "COMA")


def build_rattachements(cog_df: pd.DataFrame) -> pd.DataFrame:
    """Construit la table ancien code INSEE → commune de rattachement.

    Le rattachement est résolu de façon transitive : une commune déléguée peut
    pointer vers une commune elle-même absorbée lors d'une fusion ultérieure.
    """
    actives = cog_df[cog_df["TYPECOM"] == "COM"].set_index("COM")
    historiques = cog_df[cog_df["TYPECOM"].isin(TYPES_HISTORIQUES)]

    parents = dict(zip(historiques["COM"], historiques["COMPARENT"], strict=True))

    lignes = []
    for code, nom in zip(historiques["COM"], historiques["LIBELLE"], strict=True):
        cible = parents.get(code)
        vus = {code}
        # Remonte la chaîne de fusions jusqu'à une commune active.
        while cible is not None and cible not in actives.index and cible not in vus:
            vus.add(cible)
            cible = parents.get(cible)

        if cible is None or cible not in actives.index:
            logger.warning(
                "%s (%s) : aucune commune active de rattachement, ignorée", code, nom,
            )
            continue

        parent = actives.loc[cible]
        lignes.append({
            "code_insee_historique": code,
            "nom_historique": nom,
            "code_insee": cible,
            "nom": parent["LIBELLE"],
            "code_departement": parent["DEP"],
        })

    result = pd.DataFrame(lignes)
    if not result.empty and result["code_insee_historique"].duplicated().any():
        raise RuntimeError("Codes historiques en double dans la table de rattachement")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Table de rattachement des communes déléguées et associées",
    )
    parser.add_argument(
        "--departement", type=str, default=None,
        help="Restreindre au département de la commune de rattachement (ex: 69)",
    )
    args = parser.parse_args()

    with timed_operation(logger, "Table de rattachement"):
        session = requests.Session()
        cog = download_cog(session)
        table = build_rattachements(cog)

        n_total = len(table)
        if args.departement:
            table = table[table["code_departement"] == args.departement]
            logger.info(
                "Filtre département %s : %d rattachements sur %d",
                args.departement, len(table), n_total,
            )

        out_path = DATA_DIR / "parquet" / "communes_historiques.parquet"
        write_parquet(table, out_path)

        logger.info("=== RAPPORT ===")
        logger.info("  Rattachements écrits : %d", len(table))
        logger.info("  Fichier              : %s", out_path)
        if len(table):
            apercu = table.head(3)[["code_insee_historique", "nom_historique", "nom"]]
            for row in apercu.to_dict("records"):
                logger.info(
                    "    %s %s → %s",
                    row["code_insee_historique"], row["nom_historique"], row["nom"],
                )


if __name__ == "__main__":
    main()
