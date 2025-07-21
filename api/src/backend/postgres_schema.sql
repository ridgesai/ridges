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
