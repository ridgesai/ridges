-- Agent Versions table
CREATE TABLE IF NOT EXISTS miner_agents (
    version_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version_num INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    score FLOAT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS banned_hotkeys (
    miner_hotkey TEXT NOT NULL,
    banned_reason TEXT,
    banned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY NOT NULL,
    version_id UUID NOT NULL REFERENCES miner_agents(version_id),
    validator_hotkey TEXT NOT NULL,
    status TEXT NOT NULL,
    terminated_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    score FLOAT,
    UNIQUE(version_id, validator_hotkey) -- Prevent duplicate evaluations for same version/validator pair
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
    status TEXT NOT NULL, -- Possible values: started, sandbox_created, patch_generated, eval_started, result_scored
    started_at TIMESTAMPTZ NOT NULL,
    sandbox_created_at TIMESTAMPTZ,
    patch_generated_at TIMESTAMPTZ,
    eval_started_at TIMESTAMPTZ,
    result_scored_at TIMESTAMPTZ
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
    )
    WHERE evaluation_id = NEW.evaluation_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing triggers if they exist
DROP TRIGGER IF EXISTS tr_update_evaluation_score ON evaluation_runs;
DROP TRIGGER IF EXISTS tr_update_miner_agent_score ON evaluations;
DROP TRIGGER IF EXISTS tr_update_miner_agent_score_on_completion ON evaluations;

-- Trigger to update evaluation score when evaluation runs are inserted or updated
CREATE TRIGGER tr_update_evaluation_score
    AFTER INSERT OR UPDATE OF solved ON evaluation_runs
    FOR EACH ROW
    EXECUTE FUNCTION update_evaluation_score();

-- Function to update miner agent score when evaluation scores are updated
CREATE OR REPLACE FUNCTION update_miner_agent_score()
RETURNS TRIGGER AS $$
BEGIN
    -- Update the miner agent's score as the average of all completed evaluation scores
    -- Exclude 0 scores and require at least 2 validators
    UPDATE miner_agents
    SET score = (
        SELECT AVG(e.score)
        FROM evaluations e
        WHERE e.version_id = NEW.version_id
        AND e.status = 'completed'
        AND e.score IS NOT NULL
        AND e.score > 0  -- Exclude 0 scores
        HAVING COUNT(DISTINCT e.validator_hotkey) >= 2  -- Require at least 2 validators
    )
    WHERE version_id = NEW.version_id;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to update miner agent score when evaluation scores are updated
CREATE TRIGGER tr_update_miner_agent_score
    AFTER UPDATE OF score ON evaluations
    FOR EACH ROW
    WHEN (OLD.score IS DISTINCT FROM NEW.score)
    EXECUTE FUNCTION update_miner_agent_score();

-- Trigger to update miner agent score when evaluation status changes to completed
CREATE TRIGGER tr_update_miner_agent_score_on_completion
    AFTER UPDATE OF status ON evaluations
    FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'completed')
    EXECUTE FUNCTION update_miner_agent_score();