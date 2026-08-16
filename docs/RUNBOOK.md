# Runbook d'exploitation

## Installation

```bash
git clone <url-du-depot>
cd logement
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Remplir les variables dans .env
```

## Structure des données

Les données brutes sont stockées dans `data/raw/` (ignoré par Git).
Les fichiers Parquet intermédiaires dans `data/parquet/`.
La base SQLite finale dans `data/logement.db`.

## Scripts d'ingestion

```bash
# DVF — département 69 (développement)
python -m src.ingest.dvf --departement 69

# DVF — France entière
python -m src.ingest.dvf --all

# DPE — lister les champs exposés par le jeu de données
python -m src.ingest.dpe --schema

# DPE — code postal (développement)
python -m src.ingest.dpe --code-postal 69100

# DPE — département entier (filtre sur le préfixe du code INSEE)
python -m src.ingest.dpe --departement 69

# DPE — France entière
python -m src.ingest.dpe --all

# Référentiel géographique
python -m src.ingest.geo

# Déploiement de la fibre (ARCEP) — produit aussi les contours de la carte
python -m src.ingest.fibre --departement 69

# Table de rattachement des communes fusionnées
python -m src.ingest.communes_historiques --departement 69

# Aides
python -m src.ingest.aides
```

## Transformation

```bash
# Rapprochement DVF ↔ DPE par adresse (préalable aux agrégats)
python -m src.transform.rapprochement --departement 69

python -m src.transform.build_aggregates

# Export du JSON embarqué dans la page
python -m src.transform.export_web --departement 69
```

## Relancer une ingestion après échec

1. Consulter les logs du workflow GitHub Actions en échec
2. Relancer le script localement avec les mêmes arguments
3. Les scripts sont idempotents : une relance écrase les données précédentes

## Restaurer une version antérieure

```bash
git log --oneline  # identifier le commit souhaité
git checkout <commit> -- data/
```

## Réagir à un dépassement de quota Cloudflare

- Workers : limite de 100 000 requêtes/jour sur l'offre gratuite
- Seuil d'alerte : 60 000 requêtes/jour
- Action : passer à l'offre payante Workers ($5/mois) ou activer la mise en cache agressive
