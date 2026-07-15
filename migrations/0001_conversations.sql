-- Conversation state: the first schema in the project (Stage 3).
--
-- Plain SQL applied by shared/migrations.py. No ORM and no migration framework
-- — the codebase talks to Postgres through raw asyncpg, and this keeps one way
-- of doing that. See ADR 0007.
--
-- Migrations are append-only: never edit an applied file, add a new one.

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT        PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id TEXT        NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    -- Ordinal within the conversation. Explicit rather than relying on the
    -- surrogate key: turn order is a domain fact, and reading it off a sequence
    -- couples correctness to sequence allocation.
    position        INTEGER     NOT NULL,
    role            TEXT        NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, position)
);

-- History is always read as "every message in this conversation, in order",
-- which is exactly this index.
CREATE INDEX IF NOT EXISTS conversation_messages_by_position
    ON conversation_messages (conversation_id, position);
