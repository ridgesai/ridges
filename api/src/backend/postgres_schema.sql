-- Agent Versions table
CREATE TABLE IF NOT EXISTS miner_agents (
    version_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    version_num INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    score FLOAT
);

CREATE TABLE IF NOT EXISTS banned_hotkeys (
    miner_hotkey TEXT NOT NULL,
    banned_reason TEXT,
    banned_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY NOT NULL,
    version_id UUID NOT NULL REFERENCES miner_agents(version_id),
    validator_hotkey TEXT NOT NULL,
    status TEXT NOT NULL, -- AARON NAMED THIS 'STATUS'. IT MAY BE A RESERVED WORD. FIRE HIM IF IT BREAKS EVERYTHING. One of (waiting, running, completed, replaced)
    terminated_reason TEXT,
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
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
    started_at TIMESTAMP NOT NULL,
    sandbox_created_at TIMESTAMP,
    patch_generated_at TIMESTAMP,
    eval_started_at TIMESTAMP,
    result_scored_at TIMESTAMP
    -- finished_at removed; last stage is result_scored_at
);

CREATE TABLE IF NOT EXISTS approved_version_ids (
    version_id UUID PRIMARY KEY REFERENCES miner_agents(version_id)
)