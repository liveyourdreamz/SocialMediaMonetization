-- 0001: posts, the app state, card timers and the originality history (M1 step 2b).
-- The full post record is kept as JSONB, validated by the pydantic schema on every
-- read and write; the columns beside it are what the app filters and sorts on.

CREATE TABLE posts (
    post_id     text PRIMARY KEY,
    channel     text NOT NULL,
    status      text NOT NULL,
    created_at  timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    revision    integer NOT NULL,  -- every save adds one; a stale save is refused
    record      jsonb NOT NULL
);
CREATE INDEX posts_status_created ON posts (status, created_at);

-- one row: the canary pause (P14) and agent 14's figures
CREATE TABLE app_state (
    id     smallint PRIMARY KEY CHECK (id = 1),
    state  jsonb NOT NULL
);

-- D34: time on card is the server's clock, from the first open per reviewer
CREATE TABLE card_opens (
    post_id    text NOT NULL REFERENCES posts (post_id) ON DELETE CASCADE,
    reviewer   text NOT NULL,
    opened_at  timestamptz NOT NULL,
    PRIMARY KEY (post_id, reviewer)
);

-- PRD 4.12: approved posts the originality gate compares against (never canaries,
-- tour posts or mock drafts; cap.gates.history decides who enters)
CREATE TABLE originality_history (
    post_id      text PRIMARY KEY,
    channel      text NOT NULL,
    script       text NOT NULL,
    approved_at  timestamptz NOT NULL
);
CREATE INDEX originality_history_at ON originality_history (approved_at);
