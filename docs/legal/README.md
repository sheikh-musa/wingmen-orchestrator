# docs/legal — durable custody of signed client documents

**Why this directory exists.** On 2026-07-25 a sweep for the client's signed agreement found
exactly one copy, in `~/Downloads` **on the Mini only** — not in git, not in the substrate, not on
the Studio, no backup, surviving only until someone cleared that folder. It was also unreachable
from the hub, so a fresh body would have concluded it was lost (cai did).

That is the CAI-578 R1 argument in miniature: we were three days into arguing that a client must be
able to leave with their data while our own copy of the contract promising it was one `rm` away.

**Custody rules for anything placed here**
1. This repo is **private** (verified via the GitHub API before the first document was added). Do not
   make it public without relocating these files first.
2. **Copy, verify, then trust** — record a SHA-256 and confirm it matches the source before deleting
   any other copy. Do not delete the source copy on the strength of a successful `scp` alone.
3. **Git history is permanent.** A document committed here cannot be quietly removed later. Only add
   documents that are genuinely business records; anything that may need to be withdrawn on request
   belongs in access-controlled storage instead, not in git.
4. **Record the path in the substrate** so a future body finds it by query rather than by filesystem
   luck — the failure mode this directory exists to prevent.

## Contents

| File | What it is | SHA-256 | Provenance |
|---|---|---|---|
| `irsyad-gazzabyte-agreement-20260518-SIGNED.pdf` | Irsyad DMS / Gazzabyte signed agreement, dated 18 May 2026. 4 pages, 167,191 bytes. | `60c80c8839258df94295a1de0b0067ac763f567dc4988ad3ec7139c459366f88` | Recovered 2026-07-25 from `Musa@100.83.21.34:/Users/sheikhmusa/Downloads/`, the only copy that existed. SHA-256 verified identical on both hosts before commit. Contents deliberately not extracted, transmitted, or quoted. |

**Related but NOT here:** `~/wingmen/wingmen-cai/` holds `irsyad-wingmen-dpa-draft.{md,html,pdf}`,
`irsyad-pdpa-pack.{pdf,html}` and `irsyad-checkout-pdpa-consent.md`. **`wingmen-cai` is not a git
repository**, so those drafts carry the same exposure this directory was created to fix. They are
drafts rather than executed documents, so they are a lower priority — but the same fix applies.
