WITH rollup AS (
    SELECT
        s.product,
        s.comparison_type AS type,
        s.audience_maturity AS audience_stage,
        AVG(s.score) / 4.0 AS breadth,
        SUM(s.score * c.weight_sovereign) / 400.0 AS sovereign_fit,
        SUM(s.score * c.weight_enterprise) / 400.0 AS enterprise_fit
    FROM scores_long AS s
    JOIN category_weights AS c
      ON c.category_id = s.category_id
    GROUP BY s.product, s.comparison_type, s.audience_maturity
),
baseline AS (
    SELECT breadth, audience_stage
    FROM rollup
    WHERE product = 'АГАТ'
)
SELECT
    r.product,
    r.type,
    r.breadth,
    r.sovereign_fit,
    r.enterprise_fit,
    r.breadth - b.breadth AS feature_gap,
    CASE
        WHEN r.product = 'АГАТ' THEN 'Baseline'
        WHEN r.breadth - b.breadth <= 0 THEN 'АГАТ шире'
        WHEN r.breadth - b.breadth <= 0.05 THEN 'Паритет'
        WHEN r.breadth - b.breadth <= 0.15 THEN 'Умеренный'
        WHEN r.breadth - b.breadth <= 0.25 THEN 'Значительный'
        ELSE 'Большой'
    END AS distance,
    r.audience_stage,
    r.audience_stage - b.audience_stage AS audience_lead
FROM rollup AS r
CROSS JOIN baseline AS b
ORDER BY r.breadth DESC, r.product;
