-- Croisement prix immobilier / étiquette DPE, par commune et type de bien.
-- Alimenté par le rapprochement d'adresses (src/transform/rapprochement.py),
-- qui porte déjà l'étiquette : aucune jointure supplémentaire n'est nécessaire.
SELECT
    code_commune AS code_insee,
    type_local,
    classe_dpe,
    COUNT(*) AS nb_observations,
    CAST(MEDIAN(
        CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE)
    ) AS INTEGER) AS prix_m2_median
FROM dvf_dpe_matched
WHERE surface_reelle_bati > 0
    AND classe_dpe IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')
GROUP BY code_commune, type_local, classe_dpe
