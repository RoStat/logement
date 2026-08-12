# Journal des décisions

## 2026-08-12 — Initialisation du projet

**Options** : monorepo vs multi-dépôts
**Choix** : monorepo unique
**Raison** : conformité avec la contrainte §4.8 du cahier des charges (tout le code dans un dépôt Git unique). Simplifie les workflows CI/CD et la gestion des dépendances entre ingestion, transformation et génération.

## 2026-08-12 — Format de stockage intermédiaire

**Options** : CSV, Parquet, SQLite
**Choix** : Parquet pour les données brutes ingérées, SQLite pour les agrégats finaux
**Raison** : Parquet offre la compression, le typage fort et la lecture colonnaire nécessaire à DuckDB. SQLite est le format natif de Cloudflare D1.
