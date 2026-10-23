-- 0002: the job queue (M1 step 3, ADR-006). Background work is a row, so a restart
-- loses nothing. The two partial unique indexes are the queue's rules in the database:
-- one active job per post and kind, and one running job per post.

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL,
    post_id      text NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    error        text NOT NULL DEFAULT '',
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
CREATE UNIQUE INDEX jobs_one_active_per_kind ON jobs (post_id, kind)
    WHERE status IN ('queued', 'running');
CREATE UNIQUE INDEX jobs_one_running_per_post ON jobs (post_id) WHERE status = 'running';
CREATE INDEX jobs_queued ON jobs (id) WHERE status = 'queued';
