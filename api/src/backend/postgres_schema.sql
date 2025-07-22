-- Agent Versions table
CREATE TABLE IF NOT EXISTS miner_agents (
    version_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version_num INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT,
    agent_summary TEXT  -- AI-generated summary of agent code describing its approach and functionality
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

-- Legacy cleanup: Drop score column and any related triggers
DROP TRIGGER IF EXISTS tr_update_miner_agent_score ON miner_agents;
DROP TRIGGER IF EXISTS tr_miner_agent_score_update ON miner_agents;
DROP TRIGGER IF EXISTS tr_score_update ON miner_agents;
DROP FUNCTION IF EXISTS update_miner_agent_score() CASCADE;

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
    status TEXT NOT NULL,
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
    run_id UUID NOT NULL REFERENCES evaluation_runs(run_id),
    messages JSONB NOT NULL,
    temperature FLOAT NOT NULL,
    model TEXT NOT NULL,
    cost FLOAT,
    response TEXT,
    total_tokens INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
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

-- Materialized view to precompute agent scores
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_scores AS
WITH latest_agents AS (
    -- Get the most recent version for each hotkey
    SELECT DISTINCT ON (miner_hotkey)
        version_id,
        miner_hotkey,
        agent_name,
        version_num,
        created_at,
        status,
        agent_summary
    FROM miner_agents
    WHERE miner_hotkey NOT IN (SELECT miner_hotkey FROM banned_hotkeys)
    ORDER BY miner_hotkey, version_num DESC, created_at DESC
),
agent_evaluations AS (
    -- Get all evaluations for these latest agents
    SELECT
        la.version_id,
        la.miner_hotkey,
        la.agent_name,
        la.version_num,
        la.created_at,
        la.status,
        la.agent_summary,
        e.set_id,
        e.score,
        e.validator_hotkey,
        (avi.version_id IS NOT NULL) as approved
    FROM latest_agents la
    LEFT JOIN approved_version_ids avi ON la.version_id = avi.version_id
    LEFT JOIN evaluations e ON la.version_id = e.version_id
        AND e.status = 'completed' 
        AND e.score IS NOT NULL
        AND e.score > 0
        AND e.validator_hotkey NOT LIKE 'i-0%'
),
-- For agents with evaluations, apply outlier logic
avg_scores AS (
    SELECT
        miner_hotkey,
        version_id,
        set_id,
        AVG(score) as avg_score
    FROM agent_evaluations
    WHERE score IS NOT NULL
    GROUP BY miner_hotkey, version_id, set_id
),
scores_with_deviation AS (
    SELECT
        ae.*,
        ABS(ae.score - avs.avg_score) AS deviation
    FROM agent_evaluations ae
    LEFT JOIN avg_scores avs ON ae.miner_hotkey = avs.miner_hotkey 
        AND ae.version_id = avs.version_id 
        AND ae.set_id = avs.set_id
    WHERE ae.score IS NOT NULL
),
max_outliers AS (
    SELECT miner_hotkey, version_id, set_id, MAX(deviation) AS max_deviation
    FROM scores_with_deviation
    GROUP BY miner_hotkey, version_id, set_id
)
SELECT
    swd.version_id,
    swd.miner_hotkey,
    swd.agent_name,
    swd.version_num,
    swd.created_at,
    swd.status,
    swd.agent_summary,
    swd.set_id,
    swd.approved,
    COUNT(DISTINCT swd.validator_hotkey) AS validator_count,
    AVG(swd.score) AS final_score
FROM scores_with_deviation swd
LEFT JOIN max_outliers mo ON swd.miner_hotkey = mo.miner_hotkey 
    AND swd.version_id = mo.version_id 
    AND swd.set_id = mo.set_id
    AND swd.deviation = mo.max_deviation
WHERE mo.max_deviation IS NULL  -- Exclude the most outlier score
GROUP BY swd.version_id, swd.miner_hotkey, swd.agent_name, swd.version_num, 
         swd.created_at, swd.status, swd.agent_summary, swd.set_id, swd.approved
HAVING COUNT(DISTINCT swd.validator_hotkey) >= 2  -- At least 2 validators
ORDER BY final_score DESC, created_at ASC;

-- Create indexes for fast querying on the materialized view
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_scores_unique ON agent_scores (version_id, set_id);
CREATE INDEX IF NOT EXISTS idx_agent_scores_set_score ON agent_scores (set_id, final_score DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_agent_scores_version ON agent_scores (version_id);
CREATE INDEX IF NOT EXISTS idx_agent_scores_hotkey ON agent_scores (miner_hotkey);
CREATE INDEX IF NOT EXISTS idx_agent_scores_approved ON agent_scores (approved, set_id, final_score DESC);

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
