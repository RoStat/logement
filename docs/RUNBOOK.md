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

# Loyers d'annonce par commune (Ministère de la Transition écologique)
python -m src.ingest.loyers --departement 69

# Déploiement de la fibre (ARCEP) — produit aussi les contours de la carte
python -m src.ingest.fibre --departement 69

# Table de rattachement des communes fusionnées
python -m src.ingest.communes_historiques --departement 69

# Aides — valide la fiche versionnée reference/aides_nationales.json
python -m src.ingest.aides

# Aides — vérifie en plus que chaque lien officiel répond
python -m src.ingest.aides --verifier-liens
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

## Chargement dans Supabase

Le schéma est créé par migration ; le chargement lit la base SQLite produite par
la transformation et réécrit chaque table dans une transaction unique.

```bash
# Renseigner d'abord SUPABASE_DB_URL (Réglages > Database > Connection string)
export SUPABASE_DB_URL='postgresql://postgres:...@db.<projet>.supabase.co:5432/postgres'
python -m src.transform.load_supabase --departement 69

# Sans connexion : produire le script pour relecture
python -m src.transform.load_supabase --departement 69 --sql charge.sql
```

Le chargement est idempotent : chaque table est vidée puis réécrite. Un échec en
cours de route laisse la base dans son état précédent.

## Intégration continue

| Workflow | Déclenchement | Rôle |
|---|---|---|
| `ci.yml` | chaque poussée et chaque demande de fusion | `ruff` puis `pytest` |
| `donnees.yml` | le 3 de chaque mois, ou à la demande | chaîne complète d'ingestion, agrégats, export et chargement |

`donnees.yml` demande le secret `SUPABASE_DB_URL` dans les réglages du dépôt
(*Settings > Secrets and variables > Actions*). Sans lui, le workflow va tout de
même au bout et produit le script SQL, ce qui garde la chaîne vérifiable sur un
dépôt fraîchement cloné.

Les sources sont mises à jour à des rythmes différents — DVF plusieurs fois par
an, l'ARCEP chaque trimestre, le COG chaque janvier. Un passage mensuel les
couvre sans les solliciter inutilement.
