# Logement — Données publiques immobilier, énergie et aides

Site web francophone qui répond à des questions paramétrées sur le logement à partir de données publiques françaises : prix de vente réels, performance énergétique du parc, aides à la rénovation.

## Sources de données

| Source | Producteur | Licence |
|---|---|---|
| DVF géolocalisé | DGFiP / Etalab | Licence Ouverte |
| DPE logements existants | ADEME | ODbL |
| Base Adresse Nationale | Etalab | ODbL |
| Code Officiel Géographique | INSEE | Licence Ouverte |

## Prérequis

- Python 3.11+
- Node.js 20+ (pour le site Astro, lots ultérieurs)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Vérification de l'installation

```bash
python -c "import duckdb, requests, pandas; print('ok')"
```

## Structure du projet

```
src/ingest/          Scripts d'ingestion (DVF, DPE, géo, aides)
src/transform/       Transformation et agrégats (DuckDB)
src/common/          Configuration, stockage, journalisation
sql/                 Requêtes DuckDB
web/                 Application Astro (site statique)
worker/              Worker Cloudflare (pages dynamiques)
tests/               Tests unitaires
data/                Données locales (ignoré par Git)
docs/                Documentation (RUNBOOK, DECISIONS)
.github/workflows/   CI/CD
```

## Conventions

- Formatage et linting : `ruff`
- Annotations de types sur les fonctions publiques
- Chaque script est exécutable seul et idempotent
- Journalisation structurée : lignes lues / retenues / durée

## Licence

Code source : à définir.
Les données utilisées sont soumises aux licences de leurs producteurs respectifs (voir `/sources` sur le site).
