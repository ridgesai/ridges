ALTER TABLE evaluation_sets
    ADD COLUMN IF NOT EXISTS problem_suite_name TEXT,
    ADD COLUMN IF NOT EXISTS benchmark_family TEXT,
    ADD COLUMN IF NOT EXISTS execution_spec JSONB;

ALTER TABLE evaluation_runs
    ADD COLUMN IF NOT EXISTS benchmark_family TEXT,
    ADD COLUMN IF NOT EXISTS execution_spec JSONB;

DROP TABLE IF EXISTS infinite_swe_problems;

ALTER TABLE evaluation_sets DROP CONSTRAINT IF EXISTS evaluation_sets_pkey;
ALTER TABLE evaluation_sets
    ALTER COLUMN problem_name SET NOT NULL;
ALTER TABLE evaluation_sets
    ADD PRIMARY KEY (set_id, set_group, problem_name);

UPDATE evaluation_sets
SET benchmark_family = COALESCE(
    benchmark_family,
    execution_spec->>'benchmark_family',
    execution_spec->>'problem_suite_name',
    problem_suite_name,
    'custom'
)
WHERE benchmark_family IS NULL OR benchmark_family = '';

UPDATE evaluation_sets
SET problem_suite_name = COALESCE(
    problem_suite_name,
    execution_spec->>'problem_suite_name',
    benchmark_family
)
WHERE problem_suite_name IS NULL OR problem_suite_name = '';

UPDATE evaluation_runs
SET benchmark_family = COALESCE(
    benchmark_family,
    execution_spec->>'benchmark_family',
    execution_spec->>'problem_suite_name',
    'custom'
)
WHERE benchmark_family IS NULL OR benchmark_family = '';
