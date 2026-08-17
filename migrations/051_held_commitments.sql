-- 051_held_commitments.sql
-- A promise stops living in a process and starts living in the substrate.
--
-- CAI-RESP-1029 (2026-08-17), operator op#13770/13776/13788. Build 2 of 2, owner orch-console.
--
-- THE INCIDENT THIS EXISTS FOR: cc-irsyad-coord held a client deadline for Shuk (the CAI-928
--   authorisation document) as a BACKGROUND ALARM inside its own live process, with pre-cleared
--   text saved alongside it. The pre-cleared TEXT survived its recycle. The TRIGGER did not.
--   The commitment then fell back on "a fresh body notices a file", which is how two other items
--   dropped out of the SRE's queue across its reset the same night.
--
-- cai's ruling, and it is better than the answer I proposed: this is a DURABILITY defect, NOT a
--   headcount one. A second coordinator does not fix it — a lone coordinator recycling still drops
--   threads. The fix is that the clock lives somewhere that outlives the body holding it.
--
-- THIS IS THE OPERATOR-MESSAGE DURABLE-LOG PATTERN APPLIED TO COMMITMENTS (CAI-RESP-277): delivery
--   is guaranteed by the LOG plus reconciliation, never by the keystroke. There, a dropped nudge
--   could no longer lose an operator message. Here, a recycled body can no longer lose a promise.
--
-- PREREQUISITE, per CAI-RESP-1029: no build lane may become ephemeral until this exists. A fleet of
--   bodies that are destroyed on purpose cannot hold commitments in their heads by definition.
--
-- ─────────────────────────────────────────────────────────────────────────────────────────────
-- THE ONE DISTINCTION THIS SCHEMA REFUSES TO BLUR: **fired is not discharged.**
--   fired      = the trigger surfaced it to someone. A machine event.
--   discharged = the thing actually reached the human it was promised to. A claim about the world.
--   These are separate columns on purpose. Tonight's whole class of error is treating the first as
--   evidence of the second — "merged" read as in-production, "deployed" read as rendered, a
--   launchd exit status read as a run record. A commitment that FIRED into a dead body's pane is
--   not kept, and this table must never be able to say otherwise.
--
-- WHAT THIS DOES NOT DO, deliberately:
--   * It does NOT fire anything. Storage only. The sweeper is a separate, separately-reviewable
--     thing, and per CAI-RESP-1029 it does not count as existing until it has been EXERCISED —
--     built + loaded + observed to actually fire. `residency_sweep` was authored, committed, had
--     its plist written on 2026-07-02, and never ran for six weeks. A table with no proven sweeper
--     is a nicer-looking version of that failure, so this migration is explicitly HALF the build.
--   * It does NOT auto-discharge on fire. Only a body that believes the promise was actually kept
--     writes `discharged_by`, and it must name itself.
--   * It does NOT delete. A cancelled or discharged promise stays readable — the audit question
--     "what did we promise this client and when" must survive the promise being over.

BEGIN;

CREATE TABLE IF NOT EXISTS held_commitments (
    id              bigserial PRIMARY KEY,

    -- WHO IS ON THE HOOK. Deliberately a plain agent id, not an FK: the whole point is that this
    -- row outlives its holder, and a body can be stood down (op#13757 elasticity) or recycled while
    -- the promise stands. An FK to a live registry would let a dead body take its promise with it.
    owner_agent     text        NOT NULL,

    title           text        NOT NULL,

    -- The thing to actually do or say when it comes due — e.g. pre-cleared client text. Kept WITH
    -- the clock, because the Shuk incident split them: the text was saved, the clock was not, and a
    -- payload nothing will ever fire is just a file nobody reads.
    payload         text,

    due_at          timestamptz NOT NULL,

    status          text        NOT NULL DEFAULT 'pending',

    -- Provenance: the op# / bus id / thread this promise came from, so a fresh body can go read the
    -- conversation rather than act on a one-line summary of it.
    source_ref      text,
    channel_tag     text,

    created_by      text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    fired_at        timestamptz,

    discharged_at   timestamptz,
    discharged_by   text,
    discharge_note  text,

    cancelled_at    timestamptz,
    cancel_reason   text,

    CONSTRAINT held_commitments_status_check
        CHECK (status IN ('pending', 'fired', 'discharged', 'cancelled')),

    -- A discharge must NAME ITSELF. "It got handled" with no author is the shape of every
    -- self-declared done this fleet has been burned by; CAI-RESP-978/986 §1 is that a control is
    -- not satisfied until it has been observed, by someone.
    CONSTRAINT held_commitments_discharge_is_attributable
        CHECK (status <> 'discharged'
               OR (discharged_by IS NOT NULL AND discharged_at IS NOT NULL)),

    -- A cancellation must give a reason. Silently dropping a promise to a client is the failure
    -- mode, not the escape hatch.
    CONSTRAINT held_commitments_cancel_has_reason
        CHECK (status <> 'cancelled'
               OR (cancel_reason IS NOT NULL AND cancelled_at IS NOT NULL)),

    -- Firing is a machine event and must be stamped when claimed.
    CONSTRAINT held_commitments_fired_is_stamped
        CHECK (status <> 'fired' OR fired_at IS NOT NULL)
);

-- The sweeper's only hot query: what is due and still owed. Partial index so the table can grow a
-- long discharged history without slowing the one read that has a clock on it.
CREATE INDEX IF NOT EXISTS held_commitments_due_idx
    ON held_commitments (due_at)
    WHERE status IN ('pending', 'fired');

CREATE INDEX IF NOT EXISTS held_commitments_owner_idx
    ON held_commitments (owner_agent)
    WHERE status IN ('pending', 'fired');

COMMENT ON TABLE held_commitments IS
    'Durable promises with a clock. CAI-RESP-1029: a commitment must outlive the body holding it — '
    'the Shuk deadline died because its alarm lived in a live process. fired <> discharged: firing '
    'is a machine event, discharge is a claim that a human actually got it.';

COMMENT ON COLUMN held_commitments.fired_at IS
    'The trigger surfaced this to someone. NOT evidence the promise was kept — see discharged_at.';
COMMENT ON COLUMN held_commitments.discharged_at IS
    'The promise actually reached the person it was made to. Requires discharged_by; never set '
    'automatically by the sweeper.';
COMMENT ON COLUMN held_commitments.owner_agent IS
    'Plain text, deliberately not an FK — the row must survive its owner being recycled or stood '
    'down (op#13757), which is the entire point of the table.';

-- ── The re-hydration read ────────────────────────────────────────────────────────────────────
-- What ANY body or trigger asks after a recycle: "what is owed, and is it late?" A fresh body must
-- be able to answer that from the substrate alone, with no memory of having made the promise.
CREATE OR REPLACE VIEW held_commitments_due AS
SELECT
    hc.id,
    hc.owner_agent,
    hc.title,
    hc.payload,
    hc.due_at,
    hc.status,
    hc.source_ref,
    hc.channel_tag,
    hc.fired_at,
    (now() - hc.due_at)                                        AS overdue_by,
    (hc.status = 'fired' AND hc.discharged_at IS NULL)         AS fired_but_not_discharged
FROM held_commitments hc
WHERE hc.status IN ('pending', 'fired')
  AND hc.due_at <= now()
ORDER BY hc.due_at;

COMMENT ON VIEW held_commitments_due IS
    'Re-hydration read for a fresh body: everything owed and now due. fired_but_not_discharged is '
    'the column that matters — it is the promise a trigger already shouted about into a pane that '
    'may no longer exist.';

COMMIT;
