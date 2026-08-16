"""Lot 4 — Aides publiques à la rénovation et au logement.

Il n'existe pas de jeu de données ouvert national décrivant les aides : l'API
Aides-territoires exige un jeton, et data.gouv.fr ne propose que des jeux
locaux. Le lot repose donc sur une fiche de référence versionnée dans le dépôt,
`reference/aides_nationales.json`, dont les modifications se relisent en diff.

**Aucun montant n'y figure, et ce script le vérifie.** L'éligibilité et les
sommes dépendent des revenus du foyer, de sa composition, des travaux, de l'âge
du logement et du statut d'occupation — rien de tout cela n'est connu du site.
Les barèmes changent par ailleurs par décret. Annoncer un chiffre reviendrait à
induire un utilisateur en erreur sur son budget ; seul le simulateur officiel
fait foi.

Usage :
    python -m src.ingest.aides
    python -m src.ingest.aides --verifier-liens
"""

import argparse
import json
import re

import pandas as pd
import requests

from src.common.config import PARQUET_DIR, PROJECT_ROOT
from src.common.logging import get_logger, timed_operation
from src.common.storage import write_parquet

logger = get_logger("ingest.aides")

FICHE = PROJECT_ROOT / "reference" / "aides_nationales.json"

CHAMPS_DISPOSITIF = {"id", "nom", "resume", "pour_qui", "conditions", "source", "verifie_le"}

BENEFICIAIRES = {"locataire", "proprietaire_occupant", "proprietaire_bailleur"}

# Un montant en euros, ou un pourcentage chiffré : c'est exactement ce que la
# fiche ne doit pas contenir.
MONTANT = re.compile(r"\d[\d\s .,]*\s*(?:€|euros?)|\d+([.,]\d+)?\s*%", re.I)

EN_TETE = {"User-Agent": "logement-bot (+https://monlogement69.netlify.app)"}


def charger() -> dict:
    return json.loads(FICHE.read_text(encoding="utf-8"))


def valider(fiche: dict) -> list[str]:
    """Retourne la liste des anomalies. Vide si la fiche est conforme."""
    anomalies = []

    for cle in ("millesime", "avertissement", "simulateur", "dispositifs"):
        if not fiche.get(cle):
            anomalies.append(f"champ absent à la racine : {cle}")
    if anomalies:
        return anomalies

    identifiants = set()
    for d in fiche["dispositifs"]:
        nom = d.get("id", "?")

        manquants = CHAMPS_DISPOSITIF - set(d)
        if manquants:
            anomalies.append(f"{nom} : champs absents {sorted(manquants)}")
            continue

        if d["id"] in identifiants:
            anomalies.append(f"{nom} : identifiant en double")
        identifiants.add(d["id"])

        inconnus = set(d["pour_qui"]) - BENEFICIAIRES
        if inconnus:
            anomalies.append(f"{nom} : bénéficiaires inconnus {sorted(inconnus)}")

        if not d["source"].get("url", "").startswith("https://"):
            anomalies.append(f"{nom} : source sans URL https")

        # Le contrôle porte sur tout le texte affiché à l'utilisateur.
        texte = " ".join([d["resume"], *d["conditions"]])
        trouve = MONTANT.search(texte)
        if trouve:
            anomalies.append(
                f"{nom} : montant chiffré interdit — « {trouve.group(0).strip()} ». "
                "Les barèmes changent par décret et l'éligibilité dépend de "
                "données que le site ne connaît pas."
            )

    return anomalies


def verifier_liens(fiche: dict) -> list[tuple[str, str, int | str]]:
    """Interroge chaque URL citée. Un lien mort vaut moins que pas de lien."""
    resultats = []
    urls = [("simulateur", fiche["simulateur"]["url"])]
    urls += [(d["id"], d["source"]["url"]) for d in fiche["dispositifs"]]

    for nom, url in urls:
        try:
            r = requests.get(url, headers=EN_TETE, timeout=30, allow_redirects=True)
            resultats.append((nom, url, r.status_code))
        except Exception as e:
            resultats.append((nom, url, type(e).__name__))
    return resultats


def aplatir(fiche: dict) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "id": d["id"],
            "nom": d["nom"],
            "resume": d["resume"],
            "pour_qui": ",".join(d["pour_qui"]),
            "conditions": " | ".join(d["conditions"]),
            "source_nom": d["source"]["nom"],
            "source_url": d["source"]["url"],
            "verifie_le": d["verifie_le"],
            "millesime": fiche["millesime"],
        }
        for d in fiche["dispositifs"]
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Aides publiques au logement")
    parser.add_argument(
        "--verifier-liens", action="store_true",
        help="Interroger chaque URL citée (nécessite un accès réseau)",
    )
    args = parser.parse_args()

    with timed_operation(logger, "Aides publiques"):
        fiche = charger()

        anomalies = valider(fiche)
        if anomalies:
            for a in anomalies:
                logger.error("  %s", a)
            raise RuntimeError(f"{len(anomalies)} anomalie(s) dans {FICHE.name}")

        out = PARQUET_DIR / "aides.parquet"
        write_parquet(aplatir(fiche), out)

        logger.info("=== RAPPORT AIDES ===")
        logger.info("  Millésime    : %s", fiche["millesime"])
        logger.info("  Dispositifs  : %d", len(fiche["dispositifs"]))
        for d in fiche["dispositifs"]:
            logger.info("    %-18s %s", d["id"], ", ".join(d["pour_qui"]))
        logger.info("  Simulateur   : %s", fiche["simulateur"]["url"])
        logger.info("  Fichier      : %s", out)

        if args.verifier_liens:
            logger.info("  Vérification des liens :")
            for nom, url, code in verifier_liens(fiche):
                niveau = logger.info if code == 200 else logger.warning
                niveau("    %-18s %-6s %s", nom, code, url)


if __name__ == "__main__":
    main()
