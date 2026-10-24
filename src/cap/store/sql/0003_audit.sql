-- 0003: the chained audit log (ADR-011). Each row carries the hash of the row before
-- it (cap.audit). The triggers make the table append-only: no UPDATE ever, and DELETE
-- only inside the retention purge, which sets cap.audit_purge for its own transaction
-- and moves the anchor to the last row it deleted.

CREATE TABLE audit_log (
    id         bigint PRIMARY KEY,
    at         timestamptz NOT NULL,
    actor      text NOT NULL,
    action     text NOT NULL,
    post_id    text NOT NULL DEFAULT '',
    detail     jsonb NOT NULL DEFAULT '{}',
    prev_hash  text NOT NULL,
    hash       text NOT NULL
);
CREATE INDEX audit_log_post ON audit_log (post_id) WHERE post_id <> '';

-- where the chain starts: the genesis, or the last row the retention purge removed
CREATE TABLE audit_anchor (
    id         smallint PRIMARY KEY CHECK (id = 1),
    prev_hash  text NOT NULL,
    first_id   bigint NOT NULL
);
INSERT INTO audit_anchor VALUES (1, '', 1);

CREATE FUNCTION audit_log_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('cap.audit_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'the audit log is append-only (% refused)', TG_OP;
END $$;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_guard();
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_guard();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_guard();
