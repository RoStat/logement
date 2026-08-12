-- Répartition des classes DPE par commune.
-- Alimenté depuis les Parquet DPE.
SELECT
    code_insee,
    classe_dpe,
    COUNT(*) AS nb_logements,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY code_insee), 1) AS part_pct,
    ROUND(AVG(conso_energie), 1) AS conso_moyenne
FROM dpe
WHERE classe_dpe IN ('A', 'B', 'C', 'D', 'E', 'F', 'G')
    AND code_insee IS NOT NULL
    AND code_insee != ''
GROUP BY code_insee, classe_dpe
