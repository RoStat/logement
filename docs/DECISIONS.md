# Journal des décisions

## 2026-08-12 — Initialisation du projet

**Options** : monorepo vs multi-dépôts
**Choix** : monorepo unique
**Raison** : conformité avec la contrainte §4.8 du cahier des charges (tout le code dans un dépôt Git unique). Simplifie les workflows CI/CD et la gestion des dépendances entre ingestion, transformation et génération.

## 2026-08-12 — Format de stockage intermédiaire

**Options** : CSV, Parquet, SQLite
**Choix** : Parquet pour les données brutes ingérées, SQLite pour les agrégats finaux
**Raison** : Parquet offre la compression, le typage fort et la lecture colonnaire nécessaire à DuckDB. SQLite est le format natif de Cloudflare D1.

## 2026-08-12 — Colonnes DPE sélectionnées

**Colonnes API ADEME (noms originaux, conservés tels quels) :**
- `N°DPE`
- `Code_postal_(BAN)`
- `Code_INSEE_(BAN)`
- `Adresse_(BAN)`
- `Etiquette_DPE`
- `Etiquette_GES`
- `Conso_5_usages_é_finale`
- `Surface_habitable_logement`
- `Année_construction`
- `Date_établissement_DPE`
- `Coordonnée_cartographique_X_(BAN)`
- `Coordonnée_cartographique_Y_(BAN)`

**Raison** : sélection stricte via le paramètre `select` pour minimiser le volume transféré. Les noms comportent des accents, des parenthèses et des caractères spéciaux — ils sont consignés ici conformément au §Lot 2.

## 2026-08-12 — URL des sources de données

| Source | URL |
|---|---|
| DVF géolocalisé | `https://files.data.gouv.fr/geo-dvf/latest/csv/` |
| DPE ADEME | `https://data.ademe.fr/data-fair/api/v1/datasets/dpe-v2-logements-existants/lines` |
| COG INSEE (via data.gouv.fr) | `https://www.data.gouv.fr/api/1/datasets/code-officiel-geographique-cog/` |
| BAN géocodage | `https://api-adresse.data.gouv.fr` |

**Note** : les scripts découvrent dynamiquement les fichiers réels (années DVF, millésime COG) et valident le schéma à l'exécution. Les URL de base sont stables mais les chemins exacts des fichiers évoluent.
