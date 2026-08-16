-- Agrégats immobiliers par voie (longue traîne).
-- id_voie concatène le code commune et le code FANTOIR de la voie ; le libellé
-- est conservé pour l'affichage, une adresse n'ayant aucun sens sans son nom.
SELECT
    id_voie,
    ANY_VALUE(code_commune) AS code_insee,
    ANY_VALUE(adresse_nom_voie) AS nom_voie,
    COUNT(*) AS nb_ventes,
    CAST(MEDIAN(
        CAST(valeur_fonciere AS DOUBLE) / CAST(surface_reelle_bati AS DOUBLE)
    ) AS INTEGER) AS prix_m2_median,
    MAX(CAST(date_mutation AS DATE)) AS derniere_vente
FROM dvf_geocoded
WHERE id_voie IS NOT NULL
    AND surface_reelle_bati > 0
GROUP BY id_voie
