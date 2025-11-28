DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'agentstatus') THEN
        CREATE TYPE AgentStatus AS ENUM (
            'cancelled',
            'screening_1',
            'failed_screening_1',
            'screening_2',
            'failed_screening_2',
            'evaluating',
            'finished'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationsetgroup') THEN
        CREATE TYPE EvaluationSetGroup AS ENUM (
            'screener_1',
            'screener_2',
            'validator'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationrunstatus') THEN
        CREATE TYPE EvaluationRunStatus AS ENUM (
            'pending',
            'initializing_agent',
            'running_agent',
            'initializing_eval',
            'running_eval',
            'finished',
            'error'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationrunlogtype') THEN
        CREATE TYPE EvaluationRunLogType AS ENUM (
            'agent',
            'eval'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evaluationstatus') THEN
        CREATE TYPE EvaluationStatus AS ENUM (
            'running',
            'success',
            'failure'
        );
    END IF;
    
END $$;

CREATE TABLE IF NOT EXISTS agents (
    agent_id UUID NOT NULL PRIMARY KEY,
    miner_hotkey TEXT NOT NULL,
    name TEXT NOT NULL,
    version_num INTEGER NOT NULL,
    status AgentStatus,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ip_address TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_miner_hotkey_version ON agents (miner_hotkey, agent_id);

CREATE TABLE IF NOT EXISTS banned_hotkeys (
    miner_hotkey TEXT NOT NULL,
    banned_reason TEXT,
    banned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_banned_hotkeys_miner_hotkey ON banned_hotkeys (miner_hotkey);

CREATE TABLE IF NOT EXISTS benchmark_agent_ids (
    agent_id UUID PRIMARY KEY REFERENCES agents(agent_id),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unapproved_agent_ids (
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    unapproved_reason TEXT,
    unapproved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_unapproved_agent_ids_agent_id ON unapproved_agent_ids (agent_id);

CREATE TABLE IF NOT EXISTS evaluation_sets (
    set_id INTEGER NOT NULL,
    set_group EvaluationSetGroup NOT NULL,
    problem_name TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (set_id, set_group, problem_name)
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID NOT NULL PRIMARY KEY,
    agent_id UUID NOT NULL REFERENCES agents,
    validator_hotkey TEXT NOT NULL,
    set_id INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP WITH TIME ZONE,
    evaluation_set_group EvaluationSetGroup NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluations_id ON evaluations (evaluation_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_agent_id ON evaluations (agent_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_validator_pattern ON evaluations (validator_hotkey text_pattern_ops);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    evaluation_run_id UUID NOT NULL PRIMARY KEY,
    evaluation_id UUID NOT NULL REFERENCES evaluations,
    problem_name TEXT NOT NULL,
    status EvaluationRunStatus,
    patch TEXT,
    test_results JSONB,
    error_code INTEGER,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    started_initializing_agent_at TIMESTAMP WITH TIME ZONE,
    started_running_agent_at TIMESTAMP WITH TIME ZONE,
    started_initializing_eval_at TIMESTAMP WITH TIME ZONE,
    started_running_eval_at TIMESTAMP WITH TIME ZONE,
    finished_or_errored_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_evaluation_id ON evaluation_runs (evaluation_id);

CREATE OR REPLACE VIEW evaluation_runs_with_cost AS
SELECT
    er.*,
    COALESCE(SUM(i.cost_usd), 0) AS total_cost_usd,
    COALESCE(SUM(i.num_input_tokens), 0) AS total_input_tokens,
    COALESCE(SUM(i.num_output_tokens), 0) AS total_output_tokens,
    COUNT(*) as num_inferences
FROM
    evaluation_runs er
    LEFT JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id
GROUP BY
    er.evaluation_run_id;

CREATE TABLE IF NOT EXISTS evaluation_run_logs (
    evaluation_run_id UUID NOT NULL,
    logs TEXT,
    type EvaluationRunLogType,
    PRIMARY KEY (evaluation_run_id, type)
);

CREATE TABLE IF NOT EXISTS inferences (
    inference_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evaluation_run_id UUID NOT NULL REFERENCES evaluation_runs(evaluation_run_id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    temperature FLOAT NOT NULL,
    messages JSONB NOT NULL,
    status_code INT,
    response TEXT,
    num_input_tokens INT,
    num_output_tokens INT,
    cost_usd FLOAT,
    request_received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    response_sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_inferences_created_provider_range
ON inferences (request_received_at, provider)
INCLUDE (response_sent_at, status_code, num_input_tokens, num_output_tokens, cost_usd)
WHERE response_sent_at IS NOT NULL AND provider IS NOT NULL;

CREATE TABLE IF NOT EXISTS approved_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(agent_id),
    set_id INT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id, set_id)
);

CREATE TABLE IF NOT EXISTS upload_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_type TEXT NOT NULL,
    hotkey TEXT,
    agent_name TEXT,
    filename TEXT,
    file_size_bytes BIGINT,
    ip_address TEXT,
    success BOOLEAN NOT NULL,
    error_type TEXT,
    error_message TEXT,
    ban_reason TEXT,
    http_status_code INT,
    agent_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evaluation_payments (
    payment_block_hash TEXT NOT NULL,
    payment_extrinsic_index TEXT NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    miner_hotkey TEXT NOT NULL,
    miner_coldkey TEXT NOT NULL,
    amount_rao INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (payment_block_hash, payment_extrinsic_index)
);

-- First view: evaluation_runs with solved status
CREATE OR REPLACE VIEW evaluation_runs_hydrated AS
SELECT
    evaluation_runs.*,
    CASE
        WHEN evaluation_runs.test_results IS NULL THEN NULL
        WHEN jsonb_array_length(evaluation_runs.test_results) = 0 THEN NULL
        WHEN (
            SELECT COUNT(*) FILTER (WHERE test->>'status' = 'pass')
            FROM jsonb_array_elements(evaluation_runs.test_results) AS test
        ) = jsonb_array_length(evaluation_runs.test_results) THEN true
        ELSE false
    END AS solved
FROM evaluation_runs;

-- Second view: Evaluations hydrated view
-- Evaluations with aggregated status and average score
CREATE OR REPLACE VIEW evaluations_hydrated AS
SELECT
    evaluations.*,
    (CASE
         WHEN EVERY(erh.status = 'finished' OR (erh.status = 'error' AND erh.error_code BETWEEN 1000 AND 1999)) THEN 'success'
         WHEN EVERY(erh.status IN ('finished', 'error')) THEN 'failure'
         ELSE 'running'
        END)::EvaluationStatus AS status,
    COUNT(*) FILTER (WHERE erh.solved)::float / COUNT(*) AS score
FROM evaluations
    INNER JOIN evaluation_runs_hydrated erh USING (evaluation_id)
GROUP BY evaluations.evaluation_id;

-- Screener 1 queue view
-- Returns agents in screening_1 status that haven't been successfully evaluated by a screener-1 validator yet
CREATE OR REPLACE VIEW screener_1_queue AS
SELECT agents.agent_id, agents.status
FROM agents
WHERE agents.status = 'screening_1'
  AND NOT EXISTS (
    SELECT 1
    FROM evaluations_hydrated
    WHERE evaluations_hydrated.agent_id = agents.agent_id
      AND evaluations_hydrated.status IN ('success', 'running')
      AND evaluations_hydrated.evaluation_set_group = 'screener_1'::EvaluationSetGroup
  )
  AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
  AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
  AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
ORDER BY agents.created_at ASC;

-- Screener 2 queue view
-- Returns agents in screening_2 status that haven't been successfully evaluated by a screener-2 validator yet
CREATE OR REPLACE VIEW screener_2_queue AS
SELECT agents.agent_id, agents.status
FROM agents
WHERE agents.status = 'screening_2'
  AND NOT EXISTS (
    SELECT 1
    FROM evaluations_hydrated
    WHERE evaluations_hydrated.agent_id = agents.agent_id
      AND evaluations_hydrated.status IN ('success', 'running')
      AND evaluations_hydrated.evaluation_set_group = 'screener_2'::EvaluationSetGroup
  )
  AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
  AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
  AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
ORDER BY agents.created_at ASC;

-- Validator queue view
-- Returns agents in evaluating status, from highest to lowest sc2 score
CREATE OR REPLACE VIEW validator_queue AS
WITH
    validator_eval_counts AS (
        SELECT
            agent_id,
            COUNT(*) FILTER (WHERE status = 'running') AS num_running_evals,
            COUNT(*) FILTER (WHERE status = 'success') AS num_finished_evals
        FROM evaluations_hydrated
        WHERE evaluations_hydrated.status IN ('success', 'running')
          AND evaluations_hydrated.evaluation_set_group = 'validator'::EvaluationSetGroup
        GROUP BY agent_id
    ),
    screener_2_scores AS (
        SELECT agent_id, MAX(score) AS score FROM evaluations_hydrated
        WHERE evaluations_hydrated.evaluation_set_group = 'screener_2'::EvaluationSetGroup
          AND evaluations_hydrated.status = 'success'
        GROUP BY agent_id
    )
SELECT
    agent_id,
    status,
    COALESCE(num_running_evals, 0) as num_running_evals,
    COALESCE(num_finished_evals, 0) as num_finished_evals
FROM agents
     INNER JOIN screener_2_scores USING (agent_id)
     LEFT JOIN validator_eval_counts USING (agent_id)
WHERE
    agents.status = 'evaluating'
--   TODO: Make into a constant, same as config.NUM_EVALS_PER_AGENT
    AND COALESCE(num_running_evals, 0) + COALESCE(num_finished_evals, 0) < 3
    AND agents.agent_id NOT IN (SELECT agent_id FROM benchmark_agent_ids)
    AND agents.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
    AND agents.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
ORDER BY
    screener_2_scores.score DESC,
    agents.created_at ASC,
    num_finished_evals DESC;

CREATE OR REPLACE VIEW evaluation_runs_with_cost AS
SELECT
  er.*,
  COALESCE(SUM(i.cost_usd), 0) AS total_cost
FROM
  evaluation_runs er
  LEFT JOIN inferences i ON er.evaluation_run_id = i.evaluation_run_id
GROUP BY
  er.evaluation_run_id;

CREATE TABLE IF NOT EXISTS agent_scores (
    agent_id UUID PRIMARY KEY,
    miner_hotkey TEXT NOT NULL,
    name TEXT NOT NULL,
    version_num INT4 NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    status AgentStatus NOT NULL,
    set_id INT4 NOT NULL,
    approved BOOLEAN,
    approved_at TIMESTAMPTZ,
    validator_count INT4 NOT NULL,
    final_score FLOAT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_scores_agent_id ON agent_scores (agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_scores_final_score ON agent_scores (final_score);
CREATE INDEX IF NOT EXISTS idx_agent_scores_created_at ON agent_scores (created_at);

CREATE OR REPLACE FUNCTION refresh_agent_scores_for_agent(target_agent_id UUID)
RETURNS VOID AS $$
BEGIN
    DELETE FROM agent_scores WHERE agent_id = target_agent_id;
    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH agent_evaluations AS (
        SELECT
            a.agent_id,
            a.miner_hotkey,
            a.name,
            a.version_num,
            a.created_at,
            a.status,
            e.set_id,
            e.score,
            e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) as approved,
            avi.approved_at
        FROM agents a
        INNER JOIN evaluations_hydrated e ON a.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.validator_hotkey NOT LIKE 'screener-%'
            AND e.set_id IS NOT NULL
        LEFT JOIN approved_agents avi ON a.agent_id = avi.agent_id AND e.set_id = avi.set_id
        WHERE a.agent_id = target_agent_id
          AND a.miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND a.agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    )
    SELECT
        ae.agent_id,
        ae.miner_hotkey,
        ae.name,
        ae.version_num,
        ae.created_at,
        ae.status,
        ae.set_id,
        ae.approved,
        ae.approved_at,
        COUNT(DISTINCT ae.validator_hotkey) AS validator_count,
        AVG(ae.score) AS final_score
    FROM agent_evaluations ae
    WHERE ae.set_id IS NOT NULL
    GROUP BY ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num,
             ae.created_at, ae.status, ae.set_id, ae.approved, ae.approved_at
    -- At least 2 validators
    -- NOTE: THIS PARAMETER IS TIED TO NUM_EVALS_PER_AGENT in api/config.py
    HAVING COUNT(DISTINCT ae.validator_hotkey) >= 2;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION populate_agent_scores()
RETURNS VOID AS $$
BEGIN
    TRUNCATE TABLE agent_scores;
    INSERT INTO agent_scores (
        agent_id, miner_hotkey, name, version_num, created_at, status,
        set_id, approved, approved_at, validator_count, final_score
    )
    WITH all_agents AS (
        SELECT
            agent_id,
            miner_hotkey,
            name,
            version_num,
            created_at,
            status
        FROM agents
        WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
          AND agent_id NOT IN (SELECT agent_id FROM unapproved_agent_ids)
    ),
    agent_evaluations AS (
        SELECT
            aa.agent_id,
            aa.miner_hotkey,
            aa.name,
            aa.version_num,
            aa.created_at,
            aa.status,
            e.set_id,
            e.score,
            e.validator_hotkey,
            (avi.agent_id IS NOT NULL AND avi.approved_at <= NOW()) as approved,
            avi.approved_at
        FROM all_agents aa
        INNER JOIN evaluations_hydrated e ON aa.agent_id = e.agent_id
            AND e.status = 'success'
            AND e.score IS NOT NULL
            AND e.score > 0
            AND e.validator_hotkey NOT LIKE 'screener-%'
            AND e.set_id IS NOT NULL
        LEFT JOIN approved_agents avi ON aa.agent_id = avi.agent_id AND e.set_id = avi.set_id
    )
    SELECT
        ae.agent_id,
        ae.miner_hotkey,
        ae.name,
        ae.version_num,
        ae.created_at,
        ae.status,
        ae.set_id,
        ae.approved,
        ae.approved_at,
        COUNT(DISTINCT ae.validator_hotkey) AS validator_count,
        AVG(ae.score) AS final_score
    FROM agent_evaluations ae
    WHERE ae.set_id IS NOT NULL
    GROUP BY ae.agent_id, ae.miner_hotkey, ae.name, ae.version_num,
             ae.created_at, ae.status, ae.set_id, ae.approved, ae.approved_at
    -- At least 2 validators
    -- NOTE: THIS PARAMETER IS TIED TO NUM_EVALS_PER_AGENT in api/config.py
    HAVING COUNT(DISTINCT ae.validator_hotkey) >= 2;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION refresh_agent_scores()
RETURNS TRIGGER AS $$
DECLARE
    affected_agent_id UUID;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF TG_TABLE_NAME = 'evaluations' THEN
            affected_agent_id := OLD.agent_id;
        ELSIF TG_TABLE_NAME = 'agents' THEN
            DELETE FROM agent_scores WHERE agent_id = OLD.agent_id;
            RETURN OLD;
        ELSIF TG_TABLE_NAME = 'approved_agents' THEN
            affected_agent_id := OLD.agent_id;
        ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN
            DELETE FROM agent_scores 
            WHERE agent_id IN (SELECT agent_id FROM agents WHERE miner_hotkey = OLD.miner_hotkey);
            RETURN OLD;
        ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
            affected_agent_id := OLD.agent_id;
        END IF;
    ELSIF TG_OP = 'TRUNCATE' THEN
        PERFORM populate_agent_scores();
        RETURN NULL;
    ELSE
        IF TG_TABLE_NAME = 'evaluations' THEN
            affected_agent_id := NEW.agent_id;
        ELSIF TG_TABLE_NAME = 'agents' THEN
            affected_agent_id := NEW.agent_id;
        ELSIF TG_TABLE_NAME = 'approved_agents' THEN
            affected_agent_id := NEW.agent_id;
        ELSIF TG_TABLE_NAME = 'banned_hotkeys' THEN
            DELETE FROM agent_scores 
            WHERE agent_id IN (SELECT agent_id FROM agents WHERE miner_hotkey = NEW.miner_hotkey);
            RETURN NEW;
        ELSIF TG_TABLE_NAME = 'unapproved_agent_ids' THEN
            DELETE FROM agent_scores WHERE agent_id = NEW.agent_id;
            RETURN NEW;
        END IF;
    END IF;
    
    IF affected_agent_id IS NOT NULL THEN
        PERFORM refresh_agent_scores_for_agent(affected_agent_id);
    END IF;
    
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_refresh_agent_scores ON evaluations;
CREATE TRIGGER tr_refresh_agent_scores
AFTER INSERT OR UPDATE OR DELETE
ON evaluations FOR EACH ROW 
EXECUTE PROCEDURE refresh_agent_scores();

DROP TRIGGER IF EXISTS tr_refresh_agent_scores_approved_agents ON approved_agents;
CREATE TRIGGER tr_refresh_agent_scores_approved_agents
AFTER INSERT OR UPDATE OR DELETE
ON approved_agents FOR EACH ROW 
EXECUTE PROCEDURE refresh_agent_scores();

DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned_hotkeys ON banned_hotkeys;
CREATE TRIGGER tr_refresh_agent_scores_banned_hotkeys
AFTER INSERT OR UPDATE OR DELETE
ON banned_hotkeys FOR EACH ROW 
EXECUTE PROCEDURE refresh_agent_scores();

DROP TRIGGER IF EXISTS tr_refresh_agent_scores_delete_agents ON agents;
CREATE TRIGGER tr_refresh_agent_scores_delete_agents
AFTER INSERT OR UPDATE OR DELETE
ON agents FOR EACH ROW 
EXECUTE PROCEDURE refresh_agent_scores();

DROP TRIGGER IF EXISTS tr_refresh_agent_scores_unapproved_agent_ids ON unapproved_agent_ids;
CREATE TRIGGER tr_refresh_agent_scores_unapproved_agent_ids
AFTER INSERT OR UPDATE OR DELETE
ON unapproved_agent_ids FOR EACH ROW
EXECUTE PROCEDURE refresh_agent_scores();
