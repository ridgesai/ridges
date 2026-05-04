CREATE TABLE agent_openrouter_secrets (
    agent_id UUID PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    runtime_api_key_ciphertext BYTEA NOT NULL,
    management_api_key_ciphertext BYTEA NOT NULL,
    workspace_id TEXT NOT NULL,
    api_key_label TEXT NOT NULL,
    api_key_creator_user_id TEXT NOT NULL,
    validated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
