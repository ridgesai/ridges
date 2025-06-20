-- Agents table
CREATE TABLE agents (
    agent_id UUID PRIMARY KEY NOT NULL,
    miner_hotkey TEXT NOT NULL,
    latest_version INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    last_updated TIMESTAMP NOT NULL
);

-- Agent Versions table
CREATE TABLE agent_versions (
    version_id UUID PRIMARY KEY NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(agent_id),
    version_num INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    score FLOAT
);

CREATE TABLE evaluations (
    evaluation_id UUID PRIMARY KEY NOT NULL,
    version_id UUID NOT NULL REFERENCES agent_versions(version_id),
    validator_hotkey TEXT NOT NULL,
    status TEXT NOT NULL, -- AARON NAMED THIS 'STATUS'. IT MAY BE A RESERVED WORD. FIRE HIM IF IT BREAKS EVERYTHING. One of (waiting,running, completed, timedout, disconnected, error)
    terminated_reason TEXT,
    created_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);

-- Evaluation Runs table
CREATE TABLE evaluation_runs (
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
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP
);
