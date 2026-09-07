-- Candidate plans are unique within a session, not across the database.
--
-- `candidate_plans.id` was the primary key on its own, and plan ids are
-- deterministic by design: every recovery produces `plan-A`, `plan-B`,
-- `plan-C`, `plan-B2`. That made the second recovery anywhere in the system —
-- a different session, a different production, a different film — collide with
-- the first, and `ON CONFLICT (id) DO NOTHING` swallowed it.
--
-- The result was silent, partial data loss. The audit log recorded four
-- candidates; the table kept whichever ids were not already taken. On the
-- deployed instance one session ended up holding a single plan, and the
-- recovery screen rehydrated to one card and an empty frontier while the API
-- response for the same session had all four.
--
-- The key is now (session_id, id), which is what it always meant.
--
-- Written to be re-runnable, because `bootstrap()` applies every migration on
-- each demo reset. Both foreign keys are dropped before the primary key, since
-- the one this migration itself creates depends on the index it then wants to
-- replace — the second run fails on that dependency otherwise.

ALTER TABLE approvals
    DROP CONSTRAINT IF EXISTS approvals_plan_id_fkey;

ALTER TABLE approvals
    DROP CONSTRAINT IF EXISTS approvals_session_plan_fkey;

ALTER TABLE candidate_plans
    DROP CONSTRAINT IF EXISTS candidate_plans_pkey;

ALTER TABLE candidate_plans
    ADD CONSTRAINT candidate_plans_pkey PRIMARY KEY (session_id, id);

-- An approval points at one plan in one session. The composite reference is
-- what stops it pointing at a same-named plan in a different session.
ALTER TABLE approvals
    ADD CONSTRAINT approvals_session_plan_fkey
    FOREIGN KEY (session_id, plan_id)
    REFERENCES candidate_plans (session_id, id);
