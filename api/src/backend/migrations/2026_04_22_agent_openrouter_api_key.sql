CREATE TABLE IF NOT EXISTS agent_openrouter_secrets (
    agent_id UUID PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
