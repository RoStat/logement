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

## 2026-08-15 — Échappement des filtres de l'API ADEME

**Problème** : le paramètre `qs` de data-fair est interprété avec la syntaxe
`query_string` d'Elasticsearch. Les noms de champs de l'API ADEME comportent des
parenthèses (`Code_postal_(BAN)`), qui y sont des opérateurs de groupement. Le
filtre était injecté sans échappement.

**Conséquence** : le filtre est ignoré silencieusement — l'ingestion « code
postal 69001 » aurait téléchargé la France entière sans message d'erreur.

**Choix** : échapper les caractères réservés via `escape_qs()`, et centraliser la
construction des paramètres dans `build_query_params()`, testée hors ligne.

**Reste à vérifier en conditions réelles** : la syntaxe exacte acceptée par
data-fair n'a pas pu être confirmée (accès réseau aux sources bloqué dans
l'environnement de développement distant). À valider au premier appel réel en
comparant le nombre de lignes retournées au périmètre demandé.

## 2026-08-15 — Filtre départemental du DPE : code INSEE et non code postal

**Options** : filtrer sur le préfixe du code postal, ou sur celui du code INSEE
**Choix** : code INSEE (`Code_INSEE_(BAN):69*`)
**Raison** : le préfixe du code INSEE communal désigne le département de façon
fiable. Le préfixe du code postal, lui, déborde sur les départements voisins
(zones de distribution postale), ce qui produirait un périmètre inexact.

## 2026-08-15 — Fragilité des contrôles qualité face aux valeurs aberrantes

**Constat** : le contrôle `prix_m2_median ∈ [100, 40000]` est bloquant. Vérifié
sur jeu de données synthétique : 4 lignes aberrantes sur 1330 (0,3 %) suffisent à
faire échouer l'intégralité du build.

**Risque** : les cessions à valeur symbolique (1 €, donations, ventes entre
proches) sont fréquentes dans le DVF réel et passent le filtre actuel, qui
n'exclut que `valeur_fonciere <= 0`.

**Décision reportée** : arbitrage attendu au STOP-1. Deux pistes — écarter les
prix au m² implausibles dès l'ingestion (préserve le caractère bloquant du
contrôle, mais modifie le taux de rétention, qui est un critère de validation),
ou rendre le contrôle non bloquant au profit d'un rapport d'anomalies.
