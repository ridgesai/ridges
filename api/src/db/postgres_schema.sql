-- Agents table
CREATE TABLE IF NOT EXISTS agents (
    agent_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    name TEXT NOT NULL,
    latest_version INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    last_updated TIMESTAMP NOT NULL
);

-- Agent Versions table
CREATE TABLE IF NOT EXISTS agent_versions (
    version_id UUID PRIMARY KEY NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    version_num INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    score FLOAT
);

-- Add performance indexes on common read paths
CREATE INDEX IF NOT EXISTS idx_agents_miner_hotkey ON agents(miner_hotkey);
CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_id ON agent_versions(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_id_version_num ON agent_versions(agent_id, version_num DESC);
CREATE INDEX IF NOT EXISTS idx_agent_versions_agent_id_created_at ON agent_versions(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY NOT NULL,
    version_id UUID NOT NULL REFERENCES agent_versions(version_id),
    validator_hotkey TEXT NOT NULL,
    status TEXT NOT NULL, -- AARON NAMED THIS 'STATUS'. IT MAY BE A RESERVED WORD. FIRE HIM IF IT BREAKS EVERYTHING. One of (waiting, running, completed, replaced)
    terminated_reason TEXT,
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
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
    status TEXT NOT NULL, -- Possible values: started, sandbox_created, patch_generated, eval_started, result_scored
    started_at TIMESTAMP NOT NULL,
    sandbox_created_at TIMESTAMP,
    patch_generated_at TIMESTAMP,
    eval_started_at TIMESTAMP,
    result_scored_at TIMESTAMP
    -- finished_at removed; last stage is result_scored_at
);

-- Weights History table
CREATE TABLE IF NOT EXISTS weights_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    time_since_last_update INTERVAL,
    miner_weights JSONB NOT NULL -- Stores {miner_hotkey: weight} pairs dynamically
);

-- Validator Logs table
CREATE TABLE IF NOT EXISTS validator_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_log_id TEXT NOT NULL, -- The original ID from validator's logging.db
    validator_hotkey TEXT NOT NULL, -- Added validator hotkey
    timestamp TIMESTAMP NOT NULL,
    levelname TEXT NOT NULL,
    name TEXT NOT NULL,
    pathname TEXT NOT NULL,
    funcName TEXT NOT NULL,
    lineno INTEGER NOT NULL,
    message TEXT NOT NULL,
    active_coroutines TEXT NOT NULL, -- JSON string of active coroutines
    eval_loop_num INTEGER NOT NULL,
    received_at TIMESTAMP NOT NULL DEFAULT NOW() -- When platform received the log
);

-- Add indexes for validator logs performance
CREATE INDEX IF NOT EXISTS idx_validator_logs_validator_hotkey ON validator_logs(validator_hotkey);
CREATE INDEX IF NOT EXISTS idx_validator_logs_timestamp ON validator_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_validator_logs_levelname ON validator_logs(levelname);
CREATE INDEX IF NOT EXISTS idx_validator_logs_received_at ON validator_logs(received_at DESC);
