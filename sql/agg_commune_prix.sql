-- Prix au m² par commune et par année, toutes catégories de biens confondues.
-- Alimente la carte départementale, qui demande une valeur unique par commune
-- là où agg_commune_immo distingue maisons et appartements.
SELECT
    code_commune AS code_insee,
    CAST(EXTRACT(YEAR FROM CAST(date_mutation AS DATE)) AS INTEGER) AS annee,
    COUNT(*) AS nb_ventes,
    CAST(MEDIAN(
        CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE)
    ) AS INTEGER) AS prix_m2_median
FROM dvf
WHERE surface_reelle_bati > 0
GROUP BY code_commune, annee
