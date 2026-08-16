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

## 2026-08-16 — Détection multi-lots portée sur le fichier brut

**Constat** : la détection tournait sur le sous-ensemble déjà filtré. Mesuré sur
le 69 en 2023 : 78 mutations multi-lots détectées sur 4 344 réellement présentes,
2 300 devenues invisibles, 2 943 lignes conservées à tort (11,8 % des retenues).
La valeur foncière couvrant l'ensemble des lots, ces lignes portaient un prix au
m² surévalué.

**Choix** : la détection porte sur le fichier brut. Le caractère multi-lots est
une propriété de la mutation telle qu'enregistrée, pas du reliquat qui survit aux
filtres. Effet : 21 486 lignes écartées contre 2 039, rétention à 25,9 %.

## 2026-08-16 — Rattachement des communes fusionnées

**Constat** : le COG courant classe les communes absorbées en `COMD` (déléguées)
et `COMA` (associées), sans département ni région. DVF et DPE portent
l'historique sous ces anciens codes. Sur le seul département 69, 580 ventes et
1 599 DPE devenaient orphelins et faisaient échouer le contrôle « toute commune a
un département ». La France compte 2 105 communes déléguées.

**Choix** : rattacher l'historique à la commune actuelle — les ventes de
Pierre-Bénite remontent sur la page Oullins-Pierre-Bénite, là où l'utilisateur
les cherchera.

**Mise en œuvre** : un script distinct, `src/ingest/communes_historiques.py`,
produit la table de rattachement ; `geo.py`, validé, reste inchangé. La
résolution est transitive, une commune déléguée pouvant pointer vers une commune
elle-même absorbée par une fusion ultérieure.

## 2026-08-16 — STOP-1 : l'unité d'observation devient la mutation

**Constat** : 18,5 % des lignes retenues appartenaient à des mutations portant
plusieurs lignes sur **une même parcelle** — un logement et ses annexes, ou
plusieurs lots d'un même bien. La règle multi-lots, fondée sur le décompte de
parcelles distinctes, ne les voyait pas. Chaque ligne portant la valeur foncière
**totale**, la vente était comptée plusieurs fois et le prix au m² obtenu en
divisant le prix total par la surface d'un seul lot.

Exemple relevé : mutation `2021-1163098`, 947 230 € répétés sur 4 lignes de 60,
150, 150 et 60 m².

**Options mesurées sur le 69** :

| | Observations | Prix/m² médian | Communes ≥15 ventes | Rétention |
|---|---|---|---|---|
| A — lignes | 119 745 | 3 990 € | 251 / 275 | 25,9 % |
| B — par mutation | 105 678 | 3 871 € | 239 / 275 | 22,8 % |
| C — mutations à lot unique | 102 341 | 3 895 € | 235 / 275 | 22,1 % |

**Choix : B.** C n'écarte que 3,2 % de plus pour un écart de médiane de 0,6 %,
invisible pour le lecteur, au prix de 4 communes supplémentaires. Le site
s'adresse à des locataires évaluant un achat : le prix au m² est le seul chiffre
sur lequel tout repose, et A le surestimait systématiquement — jusqu'à 12 % sur
les communes à grosses ventes multi-lots.

**Contrepartie assumée** : 14 communes perdent leur page (251 → 237 éligibles),
soit environ 5 % de la surface SEO et de l'inventaire publicitaire.

**Effets de bord** :
- `nb_ventes` désigne enfin des ventes et non des lots. La règle de couverture
  ≥15 ventes redevient conforme à son intention.
- Les prix aberrants tombent de 6 145 à 381 : la plupart n'étaient pas des
  cessions symboliques mais des artefacts de division par la surface d'un seul
  lot. Le filtre porte désormais sur la mutation regroupée.
- 10 440 doublons stricts supprimés — DVF republie certaines lignes à l'identique.
- 493 lignes écartées pour mutation mêlant maison et appartement (142 mutations),
  la valeur foncière n'y étant attribuable ni à l'une ni à l'autre.

**Plage de rétention re-dérivée : 18–32 %** (mesure 22,8 % sur le 69). À
réexaminer à l'ingestion d'un second département : les zones rurales, plus riches
en maisons et plus pauvres en ventes multi-lots, retiendront davantage.

**Bilan désormais vérifié en deux temps**, le regroupement faisant changer
l'unité de compte en cours de traitement : un bilan en lignes jusqu'au
regroupement, un bilan en mutations ensuite.

## 2026-08-16 — Slugs qualifiés par département

**Constat** : la déduplication ajoutait un suffixe numérique attribué au fil de
la lecture du COG. Saint-Priest du Rhône portait ainsi `saint-priest-2`, parce
qu'une commune homonyme figurait plus haut dans le fichier. Le suffixe dépendait
donc de l'ordre des lignes : une mise à jour du COG pouvait échanger les URL de
deux homonymes et casser leur référencement.

**Choix** : qualifier par le code de département — `saint-priest-69`. Stable,
lisible dans une URL, et porteur de sens. Les rares homonymes d'un même
département sont départagés par le code INSEE, unique par construction.
29 communes du Rhône sont concernées.

## 2026-08-16 — Millésime affiché : le dernier disponible par commune

**Constat** : l'export figeait l'année 2024 alors que 2025 est complet dans DVF
(dernière mutation au 31 décembre) et compte davantage de ventes — 19 772 contre
17 063. Les prix affichés avaient donc un an de retard sans raison.

**Choix** : retenir pour chaque commune son dernier millésime disponible, et
l'afficher explicitement à côté du prix. Sur le Rhône, les communes se
répartissent entre 2024 et 2025 selon qu'elles ont enregistré des ventes en 2025.

## 2026-08-16 — Fibre optique : shapefile ARCEP

**Source** : jeu « Le marché du haut et très haut débit fixe (déploiements) »,
ressource communale trimestrielle. Millésime ingéré : 2026T1.

**Le champ `couv` du fichier n'est pas un taux** : il ne prend que trois valeurs
sur le Rhône (50, 80, 95). C'est un palier réglementaire. Le taux est donc
recalculé à partir de `ftth / Locaux`.

**Les contours de la carte viennent du même fichier**, et non de
`geo.api.gouv.fr` : cette API agrège Lyon en une commune unique, là où le parc et
le prix au m² diffèrent fortement d'un arrondissement à l'autre. Une seule
source, une granularité cohérente avec les agrégats.

Mesure sur le Rhône : 96,8 % des locaux raccordables, médiane communale à 97,7 %,
minimum à 71 % (Cenves). La fibre discrimine donc peu dans ce département.

## 2026-08-16 — Couleurs de la carte

**Trois rampes séquentielles monochromes**, une par mesure, vérifiées monotones
en luminance : violet pour le prix, ambre pour le DPE, sarcelle pour la fibre.
L'ancrage s'inverse en thème sombre pour que la valeur faible reste proche du
fond.

**Les couleurs réglementaires DPE ne sont pas employées sur la carte.** Elles
restent sur la barre de répartition communale, où elles désignent de vraies
classes. Les utiliser pour une *moyenne calculée* laisserait croire à un
classement officiel qui n'existe pas, et un dégradé arc-en-ciel ne se lit pas
comme une magnitude ordonnée.

**Seuils par quantiles et non par paliers réguliers** : la distribution des prix
est très asymétrique — 775 à 5 450 €/m² — et des paliers réguliers écraseraient
tout le département sur une seule teinte. Les bornes affichées en légende sont
l'étendue réelle des données, non le premier et le dernier seuil : sur la fibre,
ces derniers masquaient le minimum à 71 %.
