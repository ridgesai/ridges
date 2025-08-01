-- Agent Versions table
CREATE TABLE IF NOT EXISTS miner_agents (
    version_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version_num INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT,
    agent_summary TEXT,
    ip_address TEXT
);

-- Add agent_summary column if it doesn't exist (for existing tables)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'miner_agents' AND column_name = 'agent_summary'
    ) THEN
        ALTER TABLE miner_agents ADD COLUMN agent_summary TEXT;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS banned_hotkeys (
    miner_hotkey TEXT NOT NULL,
    banned_reason TEXT,
    banned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evaluation_sets (
    set_id INT NOT NULL,
    type TEXT NOT NULL, -- validator, screener
    swebench_instance_id TEXT NOT NULL,
    PRIMARY KEY (set_id, type, swebench_instance_id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY NOT NULL,
    version_id UUID NOT NULL REFERENCES miner_agents(version_id),
    validator_hotkey TEXT NOT NULL,
    set_id INT NOT NULL,
    status TEXT NOT NULL, -- Possible values: waiting, running, replaced, error, completed
    terminated_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    score FLOAT
);

-- Evaluation Runs table
CREATE TABLE IF NOT EXISTS evaluation_runs (
    run_id UUID PRIMARY KEY NOT NULL,
    evaluation_id UUID NOT NULL REFERENCES evaluations(evaluation_id),
    swebench_instance_id TEXT NOT NULL,
    response TEXT,
    error TEXT,
    pass_to_fail_success TEXT,
    fail_to_pass_success TEXT,
    pass_to_pass_success TEXT,
    fail_to_fail_success TEXT,
    solved BOOLEAN,
    status TEXT NOT NULL, -- Possible values: started, sandbox_created, patch_generated, eval_started, result_scored, cancelled
    started_at TIMESTAMPTZ NOT NULL,
    sandbox_created_at TIMESTAMPTZ,
    patch_generated_at TIMESTAMPTZ,
    eval_started_at TIMESTAMPTZ,
    result_scored_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ
);

-- Evaluation Run Logs table
CREATE TABLE IF NOT EXISTS evaluation_run_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    line TEXT NOT NULL
);

-- Embeddings table
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES evaluation_runs(run_id),
    input_text TEXT NOT NULL,
    cost FLOAT,
    response JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- Inference table
CREATE TABLE IF NOT EXISTS inferences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    messages JSONB NOT NULL,
    temperature FLOAT NOT NULL,
    model TEXT NOT NULL,
    cost FLOAT,
    response TEXT,
    total_tokens INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    provider TEXT,
    status_code INT
);

CREATE TABLE IF NOT EXISTS approved_version_ids (
    version_id UUID PRIMARY KEY REFERENCES miner_agents(version_id)
);

-- Weights History table
CREATE TABLE IF NOT EXISTS weights_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    time_since_last_update INTERVAL,
    miner_weights JSONB NOT NULL
);

-- Open Users table
CREATE TABLE IF NOT EXISTS open_users (
    open_hotkey TEXT NOT NULL PRIMARY KEY,
    auth0_user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS open_user_email_whitelist (
    email TEXT NOT NULL PRIMARY KEY
);

-- Trigger functions and triggers for automatic score updates

-- Function to update evaluation score when evaluation runs are updated
CREATE OR REPLACE FUNCTION update_evaluation_score()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the score for the associated evaluation based on average of solved runs
    UPDATE evaluations 
    SET score = (
        SELECT AVG(CASE WHEN solved THEN 1.0 ELSE 0.0 END)
        FROM evaluation_runs 
        WHERE evaluation_id = NEW.evaluation_id
        AND status != 'cancelled'
    )
    WHERE evaluation_id = NEW.evaluation_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS tr_update_evaluation_score ON evaluation_runs;
DROP TRIGGER IF EXISTS tr_check_evaluation_recent_version ON evaluations;
DROP TRIGGER IF EXISTS tr_update_miner_agent_score ON miner_agents;

-- Trigger to update evaluation score when evaluation runs are inserted or updated
DROP TRIGGER IF EXISTS tr_update_evaluation_score ON evaluation_runs;
CREATE TRIGGER tr_update_evaluation_score
    AFTER INSERT OR UPDATE OF solved ON evaluation_runs
    FOR EACH ROW
    EXECUTE FUNCTION update_evaluation_score();

-- Performance optimization indices for evaluations queries

-- Primary composite index for main query filtering and ordering
CREATE INDEX IF NOT EXISTS idx_evaluations_version_set_created 
ON evaluations (version_id, set_id, created_at DESC);

-- Composite index optimized for screener evaluations in CTE
CREATE INDEX IF NOT EXISTS idx_evaluations_screener_lookup 
ON evaluations (version_id, set_id, validator_hotkey, created_at DESC) 
WHERE validator_hotkey LIKE 'i-%';

-- Index for evaluation_id lookups (used in IN clause)
CREATE INDEX IF NOT EXISTS idx_evaluations_id ON evaluations (evaluation_id);

-- Pattern-based index for validator_hotkey filtering
CREATE INDEX IF NOT EXISTS idx_evaluations_validator_pattern 
ON evaluations (validator_hotkey text_pattern_ops);

-- Partial index for non-screener evaluations
CREATE INDEX IF NOT EXISTS idx_evaluations_non_screener 
ON evaluations (version_id, set_id, created_at DESC) 
WHERE validator_hotkey NOT LIKE 'i-%';

-- NEW INDICES FOR OPTIMIZED get_evaluations_with_usage_for_agent_version QUERY

-- Critical index for evaluation_runs JOIN and filtering
-- Covers: JOIN ON evaluation_id, WHERE status != 'cancelled', ORDER BY started_at
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_eval_status_started 
ON evaluation_runs (evaluation_id, status, started_at) 
WHERE status != 'cancelled';

-- Optimized index for non-cancelled runs only (partial index for better performance)
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_eval_started_non_cancelled 
ON evaluation_runs (evaluation_id, started_at) 
WHERE status != 'cancelled';

-- General index for evaluation_runs foreign key if it doesn't exist
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_evaluation_id 
ON evaluation_runs (evaluation_id);

-- Drop and recreate materialized view to ensure clean state for concurrent refresh
DROP MATERIALIZED VIEW IF EXISTS agent_scores CASCADE;

-- Recreate the materialized view
CREATE MATERIALIZED VIEW agent_scores AS
WITH all_agents AS (
    -- Get all agent versions from non-banned hotkeys
    SELECT
        version_id,
        miner_hotkey,
        agent_name,
        version_num,
        created_at,
        status,
        agent_summary
    FROM miner_agents
    WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
),
agent_evaluations AS (
    -- Get all evaluations for all agent versions
    SELECT
        aa.version_id,
        aa.miner_hotkey,
        aa.agent_name,
        aa.version_num,
        aa.created_at,
        aa.status,
        aa.agent_summary,
        e.set_id,
        e.score,
        e.validator_hotkey,
        (avi.version_id IS NOT NULL) as approved
    FROM all_agents aa
    LEFT JOIN approved_version_ids avi ON aa.version_id = avi.version_id
    INNER JOIN evaluations e ON aa.version_id = e.version_id
        AND e.status = 'completed' 
        AND e.score IS NOT NULL
        AND e.score > 0
        AND e.validator_hotkey NOT LIKE 'i-0%'
        AND e.set_id IS NOT NULL
),
filtered_scores AS (
    -- Remove the lowest score for each agent version and set combination
    SELECT 
        ae.*,
        ROW_NUMBER() OVER (
            PARTITION BY ae.version_id, ae.set_id 
            ORDER BY ae.score ASC
        ) as score_rank
    FROM agent_evaluations ae
)
SELECT
    fs.version_id,
    fs.miner_hotkey,
    fs.agent_name,
    fs.version_num,
    fs.created_at,
    fs.status,
    fs.agent_summary,
    fs.set_id,
    fs.approved,
    COUNT(DISTINCT fs.validator_hotkey) AS validator_count,
    AVG(fs.score) AS final_score
FROM filtered_scores fs
WHERE fs.set_id IS NOT NULL
    AND fs.score_rank > 1  -- Exclude the lowest score (rank 1)
GROUP BY fs.version_id, fs.miner_hotkey, fs.agent_name, fs.version_num, 
         fs.created_at, fs.status, fs.agent_summary, fs.set_id, fs.approved
HAVING COUNT(DISTINCT fs.validator_hotkey) >= 2  -- At least 2 validators
ORDER BY final_score DESC, created_at ASC;

-- Create indexes for fast querying on the materialized view
-- CRITICAL: This unique index enables CONCURRENT refresh
DROP INDEX IF EXISTS idx_agent_scores_unique;
CREATE UNIQUE INDEX idx_agent_scores_unique ON agent_scores (version_id, set_id);
CREATE INDEX idx_agent_scores_set_score ON agent_scores (set_id, final_score DESC, created_at ASC);
CREATE INDEX idx_agent_scores_version ON agent_scores (version_id);
CREATE INDEX idx_agent_scores_hotkey ON agent_scores (miner_hotkey);
CREATE INDEX idx_agent_scores_approved ON agent_scores (approved, set_id, final_score DESC);

-- Function to refresh the agent_scores materialized view
CREATE OR REPLACE FUNCTION refresh_agent_scores_view()
RETURNS TRIGGER AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY agent_scores;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Trigger to refresh materialized view when evaluations are updated
DROP TRIGGER IF EXISTS tr_refresh_agent_scores ON evaluations;
CREATE TRIGGER tr_refresh_agent_scores
    AFTER INSERT OR UPDATE OR DELETE ON evaluations
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_agent_scores_view();

-- Trigger to refresh materialized view when agent status changes
DROP TRIGGER IF EXISTS tr_refresh_agent_scores_agents ON miner_agents;
CREATE TRIGGER tr_refresh_agent_scores_agents
    AFTER UPDATE OF status ON miner_agents
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_agent_scores_view();

-- Trigger to refresh materialized view when approved agents change
DROP TRIGGER IF EXISTS tr_refresh_agent_scores_approved ON approved_version_ids;
CREATE TRIGGER tr_refresh_agent_scores_approved
    AFTER INSERT OR DELETE ON approved_version_ids
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_agent_scores_view();

-- Trigger to refresh materialized view when banned hotkeys change
DROP TRIGGER IF EXISTS tr_refresh_agent_scores_banned ON banned_hotkeys;
CREATE TRIGGER tr_refresh_agent_scores_banned
    AFTER INSERT OR DELETE ON banned_hotkeys
    FOR EACH STATEMENT
    EXECUTE FUNCTION refresh_agent_scores_view();