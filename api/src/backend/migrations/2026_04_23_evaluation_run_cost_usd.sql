ALTER TABLE evaluation_runs ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION;

DROP VIEW IF EXISTS evaluation_runs_with_cost;
CREATE VIEW evaluation_runs_with_cost AS
SELECT
    er.*,
    COALESCE(er.cost_usd, 0) AS total_cost_usd
FROM evaluation_runs er;
