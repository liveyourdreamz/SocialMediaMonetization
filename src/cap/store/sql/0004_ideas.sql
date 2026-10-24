-- 0004: the idea book (D48). Each week's ideas, the operator's decisions, and the
-- 90-day memory trend research reads so it never proposes the same topic again.
-- The record is the Idea model as JSON; the columns are what the queries filter on.

CREATE TABLE ideas (
    id          text PRIMARY KEY,
    channel     text NOT NULL,
    week        date NOT NULL,
    status      text NOT NULL
                CHECK (status IN ('shown', 'picked', 'rejected', 'expired', 'held')),
    created_at  timestamptz NOT NULL,
    record      jsonb NOT NULL
);
CREATE INDEX ideas_week ON ideas (channel, week);
CREATE INDEX ideas_recent ON ideas (channel, created_at);
CREATE INDEX ideas_shown ON ideas (week) WHERE status = 'shown';
