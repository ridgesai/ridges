-- Threshold configuration table
CREATE TABLE IF NOT EXISTS threshold_config (
    key TEXT PRIMARY KEY NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default threshold configuration values
INSERT INTO threshold_config (key, value) VALUES 
('innovation_weight', 0.25),
('decay_per_epoch', 0.05),
('frontier_scale', 0.84),
('improvement_weight', 0.30)
ON CONFLICT (key) DO NOTHING;

-- Agent Versions table
CREATE TABLE IF NOT EXISTS miner_agents (
    version_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version_num INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT,
    agent_summary TEXT,
    ip_address TEXT,
    innovation DOUBLE PRECISION
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
    score FLOAT,
    screener_score FLOAT
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
    cancelled_at TIMESTAMPTZ,
    logs TEXT -- Complete Docker container logs for debugging and analysis
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

-- Performance optimization indices for inferences queries
CREATE INDEX IF NOT EXISTS idx_inferences_created_provider_range
ON inferences (created_at, provider)
INCLUDE (finished_at, status_code, total_tokens)
WHERE finished_at IS NOT NULL AND provider IS NOT NULL;

CREATE TABLE IF NOT EXISTS approved_version_ids (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID REFERENCES miner_agents(version_id),
    set_id INT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (version_id, set_id)
);

-- Prevent accidental deletes from approved_version_ids table
CREATE OR REPLACE FUNCTION prevent_delete_approval() RETURNS TRIGGER AS $$ BEGIN RAISE EXCEPTION 'Forbidden:Unapproving agents can lead to messing up treasury-owing financial data'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS no_delete_approval_trigger ON approved_version_ids;
CREATE TRIGGER no_delete_approval_trigger BEFORE DELETE ON approved_version_ids FOR EACH ROW EXECUTE FUNCTION prevent_delete_approval();

-- Open Users table
CREATE TABLE IF NOT EXISTS open_users (
    open_hotkey TEXT NOT NULL PRIMARY KEY,
    auth0_user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    admin INT NOT NULL DEFAULT 7
);


-- Treasury hotkeys
CREATE TABLE IF NOT EXISTS treasury_wallets (
    hotkey TEXT NOT NULL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN NOT NULL DEFAULT FALSE
);

-- Platform Status Checks table
CREATE TABLE IF NOT EXISTS platform_status_checks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status TEXT NOT NULL,
    response_time_ms INT,
    response TEXT,
    error TEXT
);

-- Open User Bittensor Hotkeys table
CREATE TABLE IF NOT EXISTS open_user_bittensor_hotkeys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    open_hotkey TEXT NOT NULL REFERENCES open_users(open_hotkey),
    bittensor_hotkey TEXT,
    set_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Top agents table
CREATE TABLE IF NOT EXISTS top_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID REFERENCES miner_agents(version_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ensure version_id is nullable to allow recording periods with no top agent
ALTER TABLE top_agents ALTER COLUMN version_id DROP NOT NULL;

CREATE TABLE IF NOT EXISTS treasury_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_transaction_id UUID NOT NULL,
    sender_coldkey TEXT NOT NULL,
    destination_coldkey TEXT NOT NULL,
    staker_hotkey TEXT NOT NULL,
    amount_alpha_rao BIGINT NOT NULL,
    version_id UUID NOT NULL REFERENCES miner_agents(version_id),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extrinsic_code TEXT NOT NULL UNIQUE,
    fee BOOLEAN NOT NULL DEFAULT FALSE
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
WHERE (validator_hotkey LIKE 'screener-1-%' OR validator_hotkey LIKE 'screener-2-%' OR validator_hotkey LIKE 'i-%');

-- Index for evaluation_id lookups (used in IN clause)
CREATE INDEX IF NOT EXISTS idx_evaluations_id ON evaluations (evaluation_id);

-- Pattern-based index for validator_hotkey filtering
CREATE INDEX IF NOT EXISTS idx_evaluations_validator_pattern 
ON evaluations (validator_hotkey text_pattern_ops);

-- Partial index for non-screener evaluations
CREATE INDEX IF NOT EXISTS idx_evaluations_non_screener 
ON evaluations (version_id, set_id, created_at DESC) 
WHERE (validator_hotkey NOT LIKE 'screener-1-%' AND validator_hotkey NOT LIKE 'screener-2-%' AND validator_hotkey NOT LIKE 'i-%');

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

-- Optimized index for innovation score calculations
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_innovation_fast
ON evaluation_runs (swebench_instance_id, started_at, solved, run_id)
WHERE status = 'result_scored' AND solved = true;

-- Speeds up filtering to the miner’s version_ids
CREATE INDEX IF NOT EXISTS idx_miner_agents_miner_hotkey_version
ON miner_agents (miner_hotkey, version_id);

-- Speeds up the join lookup from tt to ma
CREATE INDEX IF NOT EXISTS idx_treasury_transactions_version
ON treasury_transactions (version_id);

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
        (avi.version_id IS NOT NULL AND avi.approved_at <= NOW()) as approved,
        avi.approved_at
    FROM all_agents aa
    INNER JOIN evaluations e ON aa.version_id = e.version_id
        AND e.status = 'completed' 
        AND e.score IS NOT NULL
        AND e.score > 0
        AND e.validator_hotkey NOT LIKE 'screener-1-%'
        AND e.validator_hotkey NOT LIKE 'screener-2-%'
        AND e.validator_hotkey NOT LIKE 'i-0%'
        AND e.set_id IS NOT NULL
    LEFT JOIN approved_version_ids avi ON aa.version_id = avi.version_id AND e.set_id = avi.set_id
),
filtered_scores AS (
    -- Remove the lowest score for each agent version and set combination
    SELECT
        ae.*,
        ROW_NUMBER() OVER (
            PARTITION BY ae.version_id, ae.set_id
            ORDER BY ae.score ASC
        ) as score_rank,
        COUNT(*) OVER (
            PARTITION BY ae.version_id, ae.set_id
        ) as total_scores
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
    fs.approved_at,
    COUNT(DISTINCT fs.validator_hotkey) AS validator_count,
    AVG(fs.score) AS final_score
FROM filtered_scores fs
WHERE fs.set_id IS NOT NULL
    AND (
        (fs.total_scores = 2 AND fs.score_rank > 0) OR
        (fs.total_scores > 2 AND fs.score_rank > 1)
    )  -- Keep all scores when only 2 exist, exclude lowest when 3+
GROUP BY fs.version_id, fs.miner_hotkey, fs.agent_name, fs.version_num, 
         fs.created_at, fs.status, fs.agent_summary, fs.set_id, fs.approved, fs.approved_at
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

-- Trigger function to update top_agents when an evaluation is marked as completed
CREATE OR REPLACE FUNCTION set_top_agent_on_completed_evaluation()
RETURNS TRIGGER AS $$
DECLARE
    latest_set_id INT;
    latest_top_version UUID;
    current_top_version UUID;
    has_current BOOLEAN;
BEGIN
    -- Only act when status is set to completed
    IF NEW.status <> 'completed' THEN
        RETURN NEW;
    END IF;

    -- Determine the latest set_id
    SELECT MAX(set_id) INTO latest_set_id FROM evaluation_sets;
    IF latest_set_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- Get the current top agent from the materialized view for the latest set
    SELECT version_id INTO latest_top_version
    FROM agent_scores
    WHERE set_id = latest_set_id
    ORDER BY final_score DESC, created_at ASC
    LIMIT 1;

    -- Fetch the most recent entry from top_agents
    SELECT version_id INTO current_top_version
    FROM top_agents
    ORDER BY created_at DESC
    LIMIT 1;
    has_current := FOUND;

    -- Insert a new top agent entry if there is no previous entry or if it differs
    -- Note: latest_top_version can be NULL if no agents qualify, which is valid
    IF NOT has_current OR current_top_version IS DISTINCT FROM latest_top_version THEN
        INSERT INTO top_agents (version_id) VALUES (latest_top_version);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to invoke the above function when an evaluation completes
DROP TRIGGER IF EXISTS tr_set_top_agent_on_completed_evaluation ON evaluations;
CREATE TRIGGER tr_set_top_agent_on_completed_evaluation
    AFTER UPDATE OF status ON evaluations
    FOR EACH ROW
    WHEN (NEW.status = 'completed')
    EXECUTE FUNCTION set_top_agent_on_completed_evaluation();

-- Approved Top Agents History
-- Table to store history of the approved top agent for the latest set_id
CREATE TABLE IF NOT EXISTS approved_top_agents_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID REFERENCES miner_agents(version_id),
    top_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    set_id INT NOT NULL
);

-- Trigger function to (re)compute the current approved top agent for the latest set
CREATE OR REPLACE FUNCTION set_approved_top_agent_if_changed()
RETURNS TRIGGER AS $$
DECLARE
    latest_set_id_from_sets INT;
    latest_set_id_from_evals INT;
    latest_set_id INT;
    latest_top_version UUID;
    last_record_set_id INT;
    last_record_version UUID;
    has_current BOOLEAN;
BEGIN
    -- Determine the latest set_id using both evaluation_sets and evaluations as a fallback
    SELECT MAX(set_id) INTO latest_set_id_from_sets FROM evaluation_sets;
    SELECT MAX(set_id) INTO latest_set_id_from_evals FROM evaluations;

    IF latest_set_id_from_sets IS NULL AND latest_set_id_from_evals IS NULL THEN
        RETURN NEW;
    END IF;

    latest_set_id := GREATEST(COALESCE(latest_set_id_from_sets, 0), COALESCE(latest_set_id_from_evals, 0));

    -- Identify the top approved agent for the latest set_id
    SELECT a.version_id INTO latest_top_version
    FROM agent_scores a
    WHERE a.set_id = latest_set_id
      AND a.version_id IN (
          SELECT version_id FROM approved_version_ids WHERE set_id = latest_set_id AND approved_at <= NOW()
      )
    ORDER BY a.final_score DESC, a.created_at ASC
    LIMIT 1;

    -- Get the most recent history entry
    SELECT set_id, version_id INTO last_record_set_id, last_record_version
    FROM approved_top_agents_history
    ORDER BY top_at DESC
    LIMIT 1;
    has_current := FOUND;

    -- Insert a new history entry if this is the first, the latest set changed, or the top agent changed
    -- Note: latest_top_version can be NULL if no approved agents qualify, which is valid
    IF NOT has_current
       OR last_record_set_id IS DISTINCT FROM latest_set_id
       OR last_record_version IS DISTINCT FROM latest_top_version THEN
        INSERT INTO approved_top_agents_history (version_id, set_id)
        VALUES (latest_top_version, latest_set_id);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Triggers to keep approved_top_agents_history up to date
-- 1) When an evaluation completes (same as top_agents)
DROP TRIGGER IF EXISTS tr_set_approved_top_agent_on_completed_evaluation ON evaluations;
CREATE TRIGGER tr_set_approved_top_agent_on_completed_evaluation
    AFTER UPDATE OF status ON evaluations
    FOR EACH ROW
    WHEN (NEW.status = 'completed')
    EXECUTE FUNCTION set_approved_top_agent_if_changed();

-- 2) When an agent is approved for a set
DROP TRIGGER IF EXISTS tr_set_approved_top_agent_on_approval ON approved_version_ids;
CREATE TRIGGER tr_set_approved_top_agent_on_approval
    AFTER INSERT ON approved_version_ids
    FOR EACH ROW
    EXECUTE FUNCTION set_approved_top_agent_if_changed();

-- 3) When a new evaluation is created (to detect an increased max set_id promptly)
DROP TRIGGER IF EXISTS tr_set_approved_top_agent_on_eval_insert ON evaluations;
CREATE TRIGGER tr_set_approved_top_agent_on_eval_insert
    AFTER INSERT ON evaluations
    FOR EACH ROW
    EXECUTE FUNCTION set_approved_top_agent_if_changed();

-- Upload Attempts table to track all upload attempts
CREATE TABLE IF NOT EXISTS upload_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_type TEXT NOT NULL, -- 'agent' or 'open-agent'
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
    version_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
