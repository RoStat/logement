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

## 2026-08-15 — Échappement des filtres de l'API ADEME *(annulée le 2026-08-16)*

Un échappement `escape_qs()` des caractères réservés `query_string` avait été
ajouté, au motif que les noms de champs comportaient des parenthèses.

**Annulée** : l'appel réel montre que le jeu `dpe03existant` expose des champs en
minuscules avec tirets bas, sans parenthèses ni accents. Le correctif traitait un
problème inexistant et a été retiré. Conservé au journal comme rappel : ne pas
inférer un schéma d'API sans l'avoir interrogé.

## 2026-08-15 — Filtre départemental du DPE *(révisée le 2026-08-16)*

Le filtre s'appuyait sur un préfixe de code INSEE avec joker (`code_insee:69*`),
faute de champ département supposé disponible.

**Révisé** : le jeu `dpe03existant` expose directement `code_departement_ban`. Le
filtre porte désormais sur ce champ, en égalité stricte. Le bricolage de préfixe
et le cas particulier corse sont supprimés.

## 2026-08-15 — Fragilité des contrôles qualité face aux valeurs aberrantes *(tranchée le 2026-08-16)*

**Constat** : le contrôle `prix_m2_median` est bloquant. Vérifié sur jeu
synthétique : 4 lignes aberrantes sur 1 330 (0,3 %) suffisaient à faire échouer
l'intégralité du build. Les cessions à valeur symbolique (1 €, donations, ventes
entre proches) sont fréquentes dans le DVF réel et passaient le filtre, qui
n'écartait que `valeur_fonciere <= 0`.

**Choix** : écarter les prix au m² implausibles dès l'ingestion, via
`mask_prix_m2` et le compteur `exclues_prix_m2_aberrant`. Les contrôles qualité
restent bloquants — ils redeviennent un filet de sécurité plutôt qu'un point de
rupture attendu.

**Bornes partagées** : `PRIX_M2_MIN` et `PRIX_M2_MAX` sont définies dans
`config.py` et lues à la fois par le filtre d'ingestion et par le contrôle
qualité. Les laisser diverger rouvrirait exactement la faille corrigée ; un test
vérifie qu'il s'agit bien de la même source.

**Effet mesuré** : sur jeu synthétique comportant 3 % de cessions symboliques,
10 lignes sur 266 sont écartées et le build passe. L'impact réel sur le taux de
rétention du 69 sera rapporté à la première ingestion réelle.

## 2026-08-16 — Migration vers le jeu `dpe03existant`

**Constat** : l'identifiant `dpe-v2-logements-existants` renvoie 404. Le jeu
courant est `dpe03existant`, dont les champs sont en minuscules avec tirets bas.

**Conséquences sur le schéma interne** :
- `conso_energie` provient désormais de `conso_5_usages_par_m2_ep`, exprimée en
  **kWh/m²/an d'énergie primaire**, là où l'ancien champ portait une consommation
  totale en énergie finale. `sql/agg_commune_dpe.sql` en calcule la moyenne : la
  grandeur est plus directement comparable entre logements, mais l'unité affichée
  sur le site devra être corrigée en conséquence.
- `annee_construction` (entier) est remplacé par `periode_construction`, une
  tranche textuelle. Aucune conversion numérique n'est donc appliquée.
- Les coordonnées cartographiques X/Y cèdent la place à `_geopoint`, accompagné de
  `statut_geocodage` et `score_ban` exploitables pour filtrer la qualité du
  géocodage lors du rapprochement DVF↔DPE.

**Surface habitable** : aucun champ de surface ne figure dans la liste confirmée.
La question reste **ouverte** — l'accès réseau aux sources est bloqué dans
l'environnement de développement distant, le `/schema` n'a pas pu être interrogé.
Une commande `python -m src.ingest.dpe --schema` a été ajoutée : elle liste les
champs du jeu et signale ceux dont le nom contient « surface ».

## 2026-08-16 — Plage de rétention DVF : 25–40 %

**Constat** : DVF compte une ligne par **lot**, pas par vente. Sur le
département 69, le seul filtre `type_local` retire 267 716 lignes sur 462 796,
soit 57,8 % du brut. Mesure réelle : 462 796 → 144 074, soit 31,1 %.

**Choix** : plage attendue ramenée de 40–70 % à 25–40 %.

## 2026-08-16 — Bilan des filtres DVF vérifié par assertion

**Constat** : `exclues_multi_lots` comptait des *mutations* et non des *lignes*.
Une mutation multi-lots portant plusieurs lignes, 2 731 lignes du 69
disparaissaient du bilan sans compteur.

**Choix** : le compteur porte sur les lignes ; `mutations_multi_lots` est conservé
à titre indicatif. `check_balance()` vérifie désormais que la somme des
exclusions augmentée des lignes retenues reconstitue exactement le volume brut,
et lève une erreur sinon — tout filtre ajouté sans instrumentation sera détecté.

Le taux de rétention est par ailleurs recalculé sur les totaux cumulés : il était
sommé d'une année sur l'autre et atteignait 155,6 %.
