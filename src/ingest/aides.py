"""Lot 4 — Ingestion des aides publiques à la rénovation et au logement.

Ce script sera complété après la note comparative des sources (voir docs/DECISIONS.md).

Sources candidates à évaluer :
1. API Aides-territoires (aides-territoires.beta.gouv.fr)
2. Jeux de données « aides » sur data.gouv.fr
3. Barèmes MaPrimeRénov' publiés par l'ANAH

Usage :
    python -m src.ingest.aides
"""

import argparse

from src.common.logging import get_logger

logger = get_logger("ingest.aides")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion des aides publiques")
    parser.add_argument(
        "--all", action="store_true", help="Télécharger toutes les aides",
    )
    parser.parse_args()

    raise NotImplementedError(
        "Lot 4 non démarré. "
        "Produire d'abord la note comparative des sources (docs/DECISIONS.md) "
        "avant de choisir l'API à intégrer."
    )


if __name__ == "__main__":
    main()
