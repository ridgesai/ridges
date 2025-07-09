-- Agents table
CREATE TABLE IF NOT EXISTS agents (
    agent_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    latest_version INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_updated TIMESTAMPTZ NOT NULL
);

-- Agent Versions table
CREATE TABLE IF NOT EXISTS agent_versions (
    version_id UUID PRIMARY KEY NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    version_num INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    score FLOAT
);

CREATE TABLE IF NOT EXISTS banned_hotkeys (
    miner_hotkey TEXT NOT NULL,
    banned_reason TEXT,
    banned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    created_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    score FLOAT,
    UNIQUE(version_id, validator_hotkey) -- Prevent duplicate evaluations for same version/validator pair
);

-- Add performance indexes for evaluations table
CREATE INDEX IF NOT EXISTS idx_evaluations_version_validator ON evaluations(version_id, validator_hotkey);
CREATE INDEX IF NOT EXISTS idx_evaluations_validator_status ON evaluations(validator_hotkey, status);
CREATE INDEX IF NOT EXISTS idx_evaluations_created_at ON evaluations(created_at);
CREATE INDEX IF NOT EXISTS idx_agent_versions_created_at ON agent_versions(created_at);

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
    -- finished_at removed; last stage is result_scored_at
);

-- Weights History table
CREATE TABLE IF NOT EXISTS weights_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    time_since_last_update INTERVAL,
    miner_weights JSONB NOT NULL -- Stores {miner_hotkey: weight} pairs dynamically
);

-- Approved Version IDs table - tracks which versions are approved for weight consideration
CREATE TABLE IF NOT EXISTS approved_version_ids (
    version_id UUID PRIMARY KEY REFERENCES agent_versions(version_id),
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Current Approved Leader table - tracks the current approved high score leader
CREATE TABLE IF NOT EXISTS current_approved_leader (
    id INT PRIMARY KEY DEFAULT 1,
    version_id UUID REFERENCES agent_versions(version_id),
    score FLOAT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1) -- Ensures only one row exists
);

-- Pending Approvals table - tracks high-scoring agents awaiting manual review
CREATE TABLE IF NOT EXISTS pending_approvals (
    version_id UUID PRIMARY KEY REFERENCES agent_versions(version_id),
    agent_name TEXT NOT NULL,
    miner_hotkey TEXT NOT NULL,
    version_num INT NOT NULL,
    score FLOAT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_at TIMESTAMPTZ
);

-- Add performance indexes for approval tables
CREATE INDEX IF NOT EXISTS idx_approved_version_ids_approved_at ON approved_version_ids(approved_at);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals(status);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_detected_at ON pending_approvals(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_version_id ON pending_approvals(version_id);
