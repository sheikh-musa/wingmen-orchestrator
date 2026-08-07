# COSEM Onboarding — Port Research (current adcda → new platform)

Reference for the onboarding-port implementation plan. Source: code audit of `~/wingmen/projects/cosem-adcda` (2026-07-08). Vite/React PWA (Capacitor wrapper) on Firebase (Auth + Firestore + Storage + Functions, `me-central1`); client-side face ML via `@vladmandic/face-api`.

## 1. Flows & route gates (`src/App.jsx`)
- `ProtectedRoute` (`:82-154`): loading→spinner; `!currentUser`→`/login`; `requiredRoles`; `requiredPermission` via `hasPermission`; **profile-completion gate** (any non-trainee w/o `displayName`→`/profile`, exempt `/profile`,`/login`,`/onboarding`).
- `HomeRoute`: trainee→`/scdf/theory`; else `Home`.
- Routes: `/onboarding` (perm `onboarding`) — **instructor-proxy trainee capture**; `/trainer-onboarding` (perm `onboarding`); `/trainer-self-onboarding` (role `trainer`). Forced self-onboarding redirect intentionally disabled for batch 18-19 (remote SG trainers).

**A. Trainee onboarding** `Onboarding.jsx` (1738 lines, instructor-operated): select batch → optional pick-from-namelist (preseed militaryId/enName/arName) → Step1 trainee headshot (face-api `tinyFaceDetector` ~8fps loop; `FACE_SCORE_MIN=0.6`, center/size/stability checks; crop 4:5 `1024x1280@0.85`; `validateFacePhoto` rejects bad) → Step2 Emirates ID front (`CardScanner` live confidence; `scanEmiratesId` callable→Google Vision OCR auto-fills enName/arName/emiratesId/dob; skippable→manual) → form (`enName`, `arName?`, `militaryId` 3-digit, `phone?`, `emiratesId` hidden, `dob` DD/MM/YYYY) → Save (Zod validate, E.164 normalize, dup-check `isMilitaryIdTaken`, `saveTrainee`, audit, **background** descriptor gen + phone-allowlist upsert). Save enabled when: trainee photo + militaryId.length===3 + enName + dob.

**B. Trainer onboarding** `TrainerOnboarding.jsx`: single selfie + name + E.164 phone → `saveTrainer` → `upsertOnboardingPhoneAllowlist` → background descriptor. No batch, no OCR.

**C. Trainer self-onboarding** `TrainerSelfOnboarding.jsx` (role trainer, self): enName (prefilled), arName **required**, read-only resolved phone; inline phone-OTP update if no verified phone; client-side descriptor (must be non-empty) → `completeTrainerSelfOnboarding` → `/trainer-attendance`.

**Login** `Login.jsx`: passkey (flag-gated) / Google / Phone OTP (+reCAPTCHA).

## 2. Face capture (ML)
- `@vladmandic/face-api` (TF.js), models from `/models`: `tinyFaceDetector`, `faceLandmark68Net`, `faceRecognitionNet`. **Entirely client-side.**
- Stores a **128-float descriptor** on trainee/trainer doc: `faceDescriptor` (number[128]), `faceDescriptorStatus` (`pending|success|error|skipped`), `faceDescriptorUpdatedAt`, `faceDescriptorError`. Raw headshot → Storage (`headshotUrl`). Descriptor gen is async/retryable (2×600ms).
- `IdentityScanner.jsx` (`/id-scan`, perm `id_scan`): builds `faceapi.FaceMatcher(descriptors, 0.62)`, live match loop, labels `trainee:<id>`/`trainer:<id>`. Liveness (`liveness.js`) default disabled.
- **Port**: descriptors only comparable if the **same face-api model+version** is kept, else re-embed everyone. 128-float vector → natural `pgvector` fit (FaceMatcher 0.62 distance → vector similarity query). Decide client vs server inference.

## 3. Auth & allowlist gates
- **Google OAuth**: `ALLOWED_GOOGLE_DOMAIN='cosem.org.sg'`; `hd` is cosmetic — real enforcement signs out non-`@cosem.org.sg` post-auth (`AuthContext.jsx:395-413`).
- **Phone OTP** (`:219-268`): normalize E.164 → **pre-check** `checkPhoneAllowlistEligibility` (no SMS if ineligible) → invisible reCAPTCHA → `signInWithPhoneNumber` → **post-OTP** `authorizePhoneSignIn` authoritative gate (throws→sign out).
- **User doc bootstrap** (`:474-543`): email users auto-create `{role:'trainer'}` doc; **phone-only users created SERVER-side** by `authorizePhoneSignIn` (avoids race — role comes from allowlist, not client). Live `onSnapshot` syncs `userRole/Profile/linkedTraineeId/linkedTrainerId/extraPermissions`.
- **Permissions**: `onSnapshot` on `config/permissions` merged with `FALLBACK_PERMISSIONS` per role; `hasPermission` (super_admin→all).
- **phoneAllowlist** service = callable wrappers; phone-OTP roles `trainee|trainer|local_instructor` (server also `skill_sheets_editor`).
- **Passkeys** (`@simplewebauthn`): `signInWithPasskey`→server mints Firebase **custom token**→`signInWithCustomToken`. Auth convenience, not onboarding data.

## 4. Data written (Firestore → Postgres targets; ALL need `org_id` + RLS)
- **`trainees`** (upsert by `(batch, militaryId)`): militaryId, emiratesId, name(UPPER mirror), enName(UPPER), arName, dob(DD/MM/YYYY), phone(E.164), batch, headshotUrl, idFrontUrl, faceDescriptor(number[128]), faceDescriptorStatus/UpdatedAt/Error, baptAgeGroup(Auto/Source), actualMilitaryId?, status(`pending_id_capture|id_captured|active`), archived, removed*?, militaryIdHistory[], isArif?, timestamp/updatedAt/updatedBy. Photos queued to IndexedDB → Storage `trainees/{batch}/{militaryId}/{headshot,id_front}.jpg` via `sync.js` (**offline-first**).
- **`trainers`** (addDoc): name, enName, arName, phone(E.164 req), headshotUrl, faceDescriptor, faceDescriptorStatus/*, linkedUserUid, createdByEmail, status:'active', archived. Headshot uploaded **synchronously**.
- **`users`** (id=auth uid): email, role(`super_admin|admin|trainer|trainee|local_instructor|skill_sheets_editor`), phone(E.164), linkedTraineeId, linkedTrainerId, trainerOnboardedAt, extraPermissions[], nested `profile{displayName,arName,phone,address{},nextOfKin{},car{},notifications{},home{}}`. Subcol `users/{uid}/passkeys/{credId}`.
- **`phoneAllowlist`** (id=E.164 minus `+`): phoneE164, role(OTP roles), userUid, traineeId?, trainerId?, displayName, enabled, source(`admin|self|onboarding|auth|self_onboarding`), disabledReason?, timestamps.
- **`pendingAdminElevations`** (id=email): email, role(`admin|super_admin`), displayName, createdBy — claimed+deleted on Google sign-in.
- **Passkey**: `users/{uid}/passkeys/{credId}` (credentialId, publicKey, counter, transports, deviceName, rpID); `passkeyChallenges/{uid|auth-uuid}` (challenge, type, expiresAt TTL); `passkeyIndex/{credId}` (uid reverse lookup).
- `config/permissions`, `config/course_info`.

## 5. Server logic (`functions/index.js` callables/triggers → Next.js server actions/route handlers + auth hooks)
`checkPhoneAllowlistEligibility` (fails-open), `authorizePhoneSignIn` (rate-limited; creates/merges users row during auth), `upsertOwnPhoneAllowlist`, `upsertOnboardingPhoneAllowlist` (perm `onboarding`), allowlist admin CRUD, `addPendingAdmin`/`claimPendingAdminElevation`/`deletePendingAdmin`, `completeTrainerSelfOnboarding` (perm `onboarding` + role trainer; finds active trainer by phone, rejects >1), `scanEmiratesId` (**Google Vision** DOCUMENT_TEXT_DETECTION, `parseIDText`→{idNumber,enName,arName,dob,dobSource}), `onTraineeCreate` trigger (re-OCR + Sheets export), passkey callables (`@simplewebauthn/server`, custom-token mint).

## 6. Port notes — non-trivial (flag, don't solve)
1. **Client-side face ML** (face-api/TF.js): keep exact model+version OR re-embed; `faceDescriptor` 128-float → **pgvector**; decide client vs server inference; WASM bundle + camera perms are PWA-specific.
2. **WebAuthn/passkeys** tied to Firebase custom tokens → Supabase has no same-shape equivalent; rebuild "mint session from assertion" on Supabase sessions; `@simplewebauthn/server` reusable in a route handler.
3. **Phone OTP provider**: Firebase Phone Auth + invisible reCAPTCHA → Supabase phone auth (Twilio/etc.); two-stage gate (pre-SMS eligibility + post-OTP authorize) + reCAPTCHA DOM are Firebase idioms.
4. **Google `@cosem.org.sg` domain restriction**: move client-signout enforcement to a server-side auth hook/RLS.
5. **Auth-time server row creation + role-from-allowlist invariant** (the deliberate no-client-create race avoidance) → Supabase auth webhook/trigger; preserve the invariant.
6. **Multi-tenant**: nothing tenant-scoped today (batch is the only partition) → `org_id` + RLS on every table.
7. **Offline-first writes**: IndexedDB photo queue + `sync.js` replay + Firestore persistent cache. A server-action model is online; loss-proof field capture on flaky networks is a real design item.
8. **Google Vision OCR** + **GAS/Sheets export** — portable to route handlers with new secret mgmt.
9. **Composite upsert keys**: trainee `(batch, militaryId)` + status no-downgrade; allowlist id = E.164-minus-plus → real Postgres unique constraints.
10. **Denormalized/derived fields**: `name` (UPPER mirror of enName), `baptAgeGroup*` from DOB at write, `displayName` mirrored across users.profile + phoneAllowlist — keep denormalized or compute in views.
