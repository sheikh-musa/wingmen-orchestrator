# cosem-ADCDA Pre-Presentation Readiness Sweep — 2026-06-28

Ultracode workflow (task w9v70orsi): 47 agents, ~1.97M tokens, 27 adversarially-confirmed findings. Map → 5-dimension review → adversarial verify → synthesize.

## Verdict
- **Skill-sheet assessment module: READY-WITH-CAVEATS** — core flow + scoring math correct; 2 demo-killers below.
- **Photo-upload: NOT-READY** for trainer onboarding / training-photos gallery (hard-blocked in prod). **Trainee onboarding capture (headshot + Emirates-ID) IS ready** on good wifi.

## MUST-FIX before the presentation (prioritized)
1. **SEED THE EXACT DEMO PROJECT** (highest risk, NOT code) — against the project the demo actually logs into (prod `cosem-adcda-cb6d9` or staging): run the 45-sheet seed → confirm 45 docs `status="approved"`; confirm ≥1 trainee batch exists; open Assess + Matrix live and watch dropdown populate / button enable / matrix render. Empty seed → dropdown empty → dead-end.
2. **INVISIBLE PRIMARY BUTTONS** (~10-min CSS, very visible) — primary buttons (Start group / Start scoring / Submit attempt / Save signature / Next student) use class `action-btn-primary` which **does not exist** → near-invisible on dark bg. Fix: `action-btn action-btn-primary` → `action-btn primary` in `SkillSheetAssess.jsx` (10 spots), `SkillSheetView.jsx`, `PickTraineeFromNamelistModal.jsx`. Eyeball after.
3. **IF demo touches trainers/gallery: ADD 2 STORAGE RULES** (~5-min) — `storage.rules` missing `trainers/` AND `trainingPhotos/` prefixes → default-deny, hard "unauthorized" on first photo. Add 2 `match` blocks mirroring `trainees/`, deploy rules, test one real headshot + one gallery upload.

(Gating: the deploy pipeline must be restored first — pin firebase-tools **15.22.1**, not 15.22.2 — or no fix ships via CI.)

## Nice-to-haves (ihsan polish, non-blocking)
- Premature red score header (correct number, premature color → neutral until threshold).
- Matrix ✓ tally counts signed (green) + awaiting-signature (blue) together → split "demonstrated / signed".
- Re-test fail shows "—%" instead of the real attempt score.
- Raw status string leak: `Status: awaiting_signature` → friendly label (labels already exist in matrix).
- Light pastel badges/chips on the dark theme → dark-theme tokens.
- Weak button hierarchy on ready screen (resolves with the button-CSS fix; give Skip `secondary`).

## What's solid
Core scoring/pass-fail/threshold math correct; trainee onboarding capture works on normal connectivity; exams-dept core surfaces (attendance/observations/incident/inventory) covered by storage rules; no data-loss/crash on happy path (reliability gaps need bad-network edges).

## PRE-GO-LIVE security/PII homework (NOT demo-visible, real before a live cohort — PDPA, cai-relevant)
- Firestore role-gap on attendance/theory results.
- Non-expiring PII image tokens.
- Google Sheets PII export.
- No retention / auto-purge.
