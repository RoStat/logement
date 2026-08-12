-- Croisement prix immobilier / classe DPE par commune.
-- Nécessite un rapprochement préalable DVF↔DPE par géocodage.
SELECT
    dvf.code_commune AS code_insee,
    dvf.type_local,
    dpe.classe_dpe,
    COUNT(*) AS nb_observations,
    CAST(MEDIAN(
        CAST(dvf.valeur_fonciere AS DOUBLE) / CAST(dvf.surface_reelle_bati AS DOUBLE)
    ) AS INTEGER) AS prix_m2_median
FROM dvf_dpe_matched AS dvf
JOIN dpe ON dvf.dpe_numero = dpe.numero_dpe
WHERE dvf.surface_reelle_bati > 0
    AND dpe.classe_dpe IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')
GROUP BY dvf.code_commune, dvf.type_local, dpe.classe_dpe
