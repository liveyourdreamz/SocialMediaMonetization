-- 0006: a failed job is tried again after a pause (review R12, PRD 3 "retry with
-- backoff, then park"): it goes back in the queue with the time it may run again.
ALTER TABLE jobs ADD COLUMN run_after timestamptz;
CREATE INDEX jobs_queued_due ON jobs (id) WHERE status = 'queued' AND run_after IS NOT NULL;
