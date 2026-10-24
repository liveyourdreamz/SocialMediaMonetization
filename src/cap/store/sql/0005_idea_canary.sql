-- 0005: an idea can be taken out of the Digest to be a canary's base (P14).
ALTER TABLE ideas DROP CONSTRAINT ideas_status_check;
ALTER TABLE ideas ADD CONSTRAINT ideas_status_check
    CHECK (status IN ('shown', 'picked', 'rejected', 'expired', 'held', 'canary'));
