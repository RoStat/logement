-- Agrégats immobiliers par commune, année et type de local.
-- Alimenté depuis les Parquet DVF filtrés.
SELECT
    code_commune AS code_insee,
    CAST(EXTRACT(YEAR FROM CAST(date_mutation AS DATE)) AS INTEGER) AS annee,
    type_local,
    COUNT(*) AS nb_ventes,
    CAST(MEDIAN(CAST(valeur_fonciere AS DOUBLE)) AS INTEGER) AS prix_median,
    CAST(MEDIAN(CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE)) AS INTEGER) AS prix_m2_median,
    CAST(QUANTILE_CONT(CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE), 0.25) AS INTEGER) AS prix_m2_p25,
    CAST(QUANTILE_CONT(CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE), 0.75) AS INTEGER) AS prix_m2_p75,
    CAST(MEDIAN(CAST(surface_reelle_bati AS DOUBLE)) AS INTEGER) AS surface_mediane
FROM dvf
WHERE surface_reelle_bati > 0
GROUP BY code_commune, annee, type_local
