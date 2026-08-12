-- Agrégats immobiliers par voie (longue traîne).
SELECT
    id_voie,
    COUNT(*) AS nb_ventes,
    CAST(MEDIAN(
        CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE)
    ) AS INTEGER) AS prix_m2_median,
    MAX(CAST(date_mutation AS DATE)) AS derniere_vente
FROM dvf_geocoded
WHERE id_voie IS NOT NULL
    AND surface_reelle_bati > 0
GROUP BY id_voie
