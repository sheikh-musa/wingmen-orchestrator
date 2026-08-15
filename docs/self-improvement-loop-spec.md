# The ideas-up loop — spec

**Owner:** Nazim (orch-console) · **Raised by:** operator op#13332 ("why didn't you suggest this then")
**Date:** 2026-08-15 · **Status:** proposal, first slice building

---

## 1. The actual problem

The operator's complaint on 2026-08-15 arrived in two parts, and the second one is the
real one.

The first part — "why am I still the one catching lanes at 100%" — is a missing gate. It
has an owner and a path (op#13050-A, Stage-1 live, Stage-2 gated). Fixable.

The second part is harder: *"isn't that SRE? … ideally the self-heal and self-improvement
continues without me having to repeat myself every day"*, and then, when I described a
better architecture than the one I had been building: **"why didn't you suggest this
then"**.

That is not a bug in a daemon. The honest diagnosis is structural:

> **Nothing in the substrate ever asks an agent what should be different.**

Every trigger an agent responds to is a task, a gate, an alert, or a review request. All
four are *reactive to work that already exists*. There is no input whose expected output
is "here is a better shape". So proposals only surface when a human's frustration creates
the opening — which means the operator is the fleet's proposal mechanism. That is exactly
the thing he is asking to stop being.

Two consequences follow, and both are visible in today's log:

1. **Agents optimise the design they are handed.** I spent the day improving a centralised
   SRE watcher without asking whether per-lane self-recycle was the better shape. The
   operator saw it first.
2. **Recurring failures are re-discovered rather than remembered.** Today alone: a client
   board frozen for a day, an auto-publisher watching a source abandoned four days
   earlier, a bloat gauge reading false-green, an agent-id with no inbox drainer, a
   ghost-vs-real composer test that silently disabled the fleet's auto-nudge. Every one
   was found by a human noticing something, not by the fleet looking.

## 2. The measure

One number, because a loop without a metric becomes a ritual:

> **operator-caught ratio** = defects the operator found first ÷ all defects found.

Today it is high — most of the list above is his. Target is zero, and the useful property
is that it cannot be gamed by working harder: the only way to move it is for the fleet to
find things earlier. It is also the literal encoding of "without me repeating myself every
day".

Secondary: **repeat-class rate** — how often a defect belongs to a class we have already
seen. A loop that works drives this down; a loop that only files tickets does not.

## 3. Design

Four parts. The bias throughout is [[feedback_enforce_process_in_code_not_promises]]: an
accepted proposal lands as a gate, a test, or a guard — never as a norm an agent promises
to remember, because norms do not survive a context reset.

### 3.1 A proposal is a first-class output

`fleet_proposals` — any agent can file one, in one command, at the moment it notices:

| field | meaning |
|---|---|
| `from_agent` | who noticed |
| `problem` | what is wrong, in one sentence |
| `proposal` | what should be different — **required**; a finding without a proposal is a bug report, which we already have a channel for |
| `evidence` | how they know (pane/log/DB — verified at source, not inferred) |
| `failure_class` | free-text class, clustered later; this is what makes repeats visible |
| `cost_signal` | who paid: `operator-caught` \| `agent-caught` \| `near-miss` |
| `status` | `new` → `triaged` → `accepted`/`rejected` → `shipped` |

The `cost_signal` field is what produces the metric in §2 without anyone maintaining a
spreadsheet.

### 3.2 Every lane self-audits at the end of a workstream

Two questions, appended to lane doctrine so it happens on every lane without a watcher:

1. What broke or nearly broke while I did this?
2. What would have caught it earlier — and is that a gate, a test, or a guard?

Cheap, distributed, and it fires at the only moment the answer is actually known. A lane
with nothing to report files nothing; silence is a valid answer and must stay cheap, or
lanes will file noise to look diligent.

This is the same shape as the per-lane self-recycle the operator proposed in op#13329:
push the behaviour into the lane rather than into something that watches the lane.

### 3.3 Triage is cai + Nazim, not the operator

Proposals accumulate; a weekly pass clusters them by `failure_class` and picks. cai owns
doctrine changes, I own mechanism changes, and only a short digest reaches the operator —
what shipped, what the two ratios did, and any fork that is genuinely his call. He should
see the loop's *output*, never its queue. Handing him a proposal queue would just be a new
thing for him to catch.

### 3.4 A standing reliability slice

The strategic half, and the one that needs his decision rather than my build.

Every item on today's list was known-but-deferred. Auto-recycle detected bloated lanes for
*months* without being armed. The reason is not disagreement about value — it is that
reliability work loses to the next feature every single time it is scheduled against one.
The loop in §3.1–3.3 will produce good proposals and they will queue behind revenue work
forever unless capacity is reserved rather than requested.

**Proposal: a fixed slice of fleet capacity — one lane-equivalent, continuously — belongs
to reliability, and its work is drawn from the top of the proposal queue.** Not a sprint,
not a cleanup week; a standing allocation, so the boring layer stops being the thing we
get to next.

This is the operator's call with cai, because it is a capacity trade against revenue work.
It is the one item here I am proposing rather than building.

## 4. Why this would have caught today's failures

Not a claim that a process makes us smart — a check that the mechanism has teeth. Against
today's five:

| failure | what would have caught it |
|---|---|
| board frozen a day | §3.2 — the lane that published it would have been asked "what would catch this earlier", and the answer is a freshness gate, which now exists |
| auto-publisher on a dead source | §3.1 repeat-class: "watcher pointed at an abandoned source" is the same class as two earlier stale-feed bugs |
| bloat gauge false-green | already caught by an agent (fc-v53) — this is the shape we want, and `cost_signal=agent-caught` is what makes it visible as a win rather than invisible |
| base agent-id with no drainer | filed as a proposal when it stalled three messages on 08-15, instead of being noted in a handoff and forgotten |
| ghost/real composer misread | §3.2 — found today only because I happened to chase a nudge refusal; the loop makes that a routine output rather than a lucky detour |

Two of five would have been caught by the loop, one already was, and two would at least
have been *remembered* rather than re-discovered. That is a real improvement and not a
claim of perfection — worth stating plainly rather than overselling.

## 5. What is being built now, and by whom

- **Nazim (me):** `fleet_proposals` + the one-command filing path + the two ratios. Small,
  and it is the part that unblocks everyone else filing.
- **cai:** ratify §3.2 into lane doctrine (doctrine is cai's, not mine) and decide whether
  a proposal-with-no-owner expires or escalates.
- **cc-fleet-health:** the weekly clustering pass and the operator digest — it already owns
  the fleet's periodic jobs, and this is one more.
- **Operator + cai:** §3.4 only.

## 6. The honest caveat

This loop can degrade into paperwork. The failure mode is agents filing proposals to look
diligent, a queue nobody drains, and a metric that measures filing rather than fixing.
Two guards: `proposal` is a required field so a filing must contain a suggested change,
and the metric in §2 counts *defects found*, not *proposals filed* — filing more without
finding earlier moves nothing.

If after a month the operator-caught ratio has not moved, this spec was wrong and should be
killed rather than tuned.
