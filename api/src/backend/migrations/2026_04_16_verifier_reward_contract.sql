ALTER TABLE evaluation_runs
    ADD COLUMN IF NOT EXISTS verifier_reward DOUBLE PRECISION;

CREATE OR REPLACE VIEW evaluation_runs_hydrated AS
SELECT
    evaluation_runs.evaluation_run_id,
    evaluation_runs.evaluation_id,
    evaluation_runs.problem_name,
    evaluation_runs.status,
    evaluation_runs.patch,
    evaluation_runs.test_results,
    evaluation_runs.error_code,
    evaluation_runs.error_message,
    evaluation_runs.created_at,
    evaluation_runs.started_initializing_agent_at,
    evaluation_runs.started_running_agent_at,
    evaluation_runs.started_initializing_eval_at,
    evaluation_runs.started_running_eval_at,
    evaluation_runs.finished_or_errored_at,
    CASE
        WHEN evaluation_runs.verifier_reward IS NOT NULL THEN evaluation_runs.verifier_reward >= 1
        WHEN evaluation_runs.test_results IS NULL THEN NULL
        WHEN jsonb_array_length(evaluation_runs.test_results) = 0 THEN NULL
        WHEN (
            SELECT COUNT(*) FILTER (WHERE test->>'status' = 'pass')
            FROM jsonb_array_elements(evaluation_runs.test_results) AS test
        ) = jsonb_array_length(evaluation_runs.test_results) THEN true
        ELSE false
    END AS solved,
    evaluation_runs.benchmark_family,
    evaluation_runs.execution_spec,
    evaluation_runs.verifier_reward
FROM evaluation_runs;
