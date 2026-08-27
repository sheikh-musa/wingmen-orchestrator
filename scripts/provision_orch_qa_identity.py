#!/usr/bin/env python3
"""provision_orch_qa_identity.py — CREATE the console-owned goumlyne QA identity.

Governance: CAI-1257/1258/1259/1260/1261. A dedicated, distinctly-labelled,
console-owned auth identity on the irsyad silo (goumlyne) so console can perform
CAI-1124 authenticated repro/QA WITHOUT ever touching a real person's account
(the CAI-1118 incident vector). This is a CREATE, never a reset.

SS6.6 posture: gated on cai's NAMED grant (CAI-1259, window waived). Console
applies + wet-logs per CAI-1124 pen-authority. Run:
    --wet-prove   BEGIN..ROLLBACK, throwaway pw, NO keychain write — proves the
                  INSERTs are valid + shows the resulting row STRUCTURE. Safe.
    --apply       REAL create (after cai's grant): generate pw -> macOS Keychain
                  -> INSERT + COMMIT -> caller runs the post-commit sign-in test.

NEVER `supabase db push`; direct psycopg only (CLAUDE.md).

Structure modelled column-by-column on an EXISTING real GoTrue user on goumlyne
(admin@qa-jumaat.test) per CAI-1260 addition 1 — same non-null columns, same
raw_app_meta_data provider shape, same identities identity_data keys.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import uuid

import psycopg2

# --- constants (all non-secret) ---
GOUMLYNE_INSTANCE_ID = "00000000-0000-0000-0000-000000000000"  # GoTrue single-instance
IRSYAD_ORG_ID = "73339164-7c1f-40ba-a093-33f1f292dd4c"          # Madrasah Irsyad Zuhri (goumlyne)
QA_EMAIL = "orch-qa+noreply@wingmen.dev"                        # non-deliverable, owned
QA_DISPLAY = "ORCH QA - AUTOMATED, NOT A REAL USER"
QA_ROLE = "preparer"                                            # CAI-1258 min-role (loads /duplicates, minors-excluded)
KEYCHAIN_SERVICE = "wingmen-orch-qa-goumlyne"                   # macOS Keychain generic-password service
KEYCHAIN_ACCOUNT = "orch-qa"
# Origin tag (CAI-1258 element 4): queryable, so this identity's test activity is
# never confused with a real preparer in any donor/minors aggregation.
ORIGIN_TAG = {"qa_identity": True, "qa_origin": "orch-console", "not_a_real_user": True}


def _dsn():
    # CAI-1225: read from os.environ (populated by the restricted store via the invoking
    # console/cai boot), NOT by parsing the shared .env file — it no longer holds the
    # write-DSN after the cutover. Fail-loud if absent (run from a store-sourced session).
    dsn = os.environ.get("GOUMLYNE_DATABASE_URL")
    if not dsn:
        raise SystemExit("GOUMLYNE_DATABASE_URL not in env — run from a store-sourced "
                         "console/cai session (~/.wingmen/private/write_dsn.env).")
    return dsn


def _store_keychain(pw: str) -> None:
    """Store the pw in the macOS Keychain (sanctioned store, CAI-1258 el.5). -U overwrites."""
    subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE,
         "-a", KEYCHAIN_ACCOUNT, "-w", pw, "-U",
         "-j", "console-owned goumlyne QA identity (CAI-1257); NOT a real user"],
        check=True,
    )


def build(cur, user_id: str, pw: str):
    """Execute the CREATE (auth.users + auth.identities + org_members). Caller owns the txn."""
    app_meta = {
        "provider": "email",
        "providers": ["email"],
        "org_memberships": [{"role": QA_ROLE, "org_id": IRSYAD_ORG_ID}],  # JWT-claim source (mig215)
        **ORIGIN_TAG,
    }
    user_meta = {"display_name": QA_DISPLAY, "email_verified": True}
    # 1) auth.users — CREATE (crypt() bcrypt, GoTrue's own scheme); email pre-confirmed.
    cur.execute(
        # confirmed_at is a GENERATED column (LEAST(email_confirmed_at, phone_confirmed_at)) —
        # never inserted; it derives from email_confirmed_at below. (wet-prove caught this.)
        # GoTrue scans confirmation_token/recovery_token/email_change_token_new/email_change
        # into NON-nullable Go strings on LOGIN — a NULL (these cols have no DEFAULT) causes
        # "Database error querying schema" 500 on sign-in. Set them to '' like a real GoTrue
        # user. (The sign-in test caught this; the wet-prove/structure-diff could not.)
        """INSERT INTO auth.users
             (instance_id, id, aud, role, email, encrypted_password,
              email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
              created_at, updated_at, is_sso_user, is_anonymous,
              confirmation_token, recovery_token, email_change_token_new, email_change)
           VALUES (%s, %s, 'authenticated', 'authenticated', %s, crypt(%s, gen_salt('bf')),
              now(), %s, %s, now(), now(), false, false,
              '', '', '', '')""",
        (GOUMLYNE_INSTANCE_ID, user_id, QA_EMAIL, pw,
         json.dumps(app_meta), json.dumps(user_meta)),
    )
    # 2) auth.identities — email provider; provider_id = user id; identity_data shape matches real user.
    identity_data = {"sub": user_id, "email": QA_EMAIL,
                     "email_verified": True, "phone_verified": False}
    # auth.identities.email is a GENERATED column (lower(identity_data->>'email')) —
    # never inserted; it derives from identity_data.email. (wet-prove caught this.)
    cur.execute(
        """INSERT INTO auth.identities
             (id, user_id, provider, provider_id, identity_data, created_at, updated_at)
           VALUES (gen_random_uuid(), %s, 'email', %s, %s, now(), now())""",
        (user_id, user_id, json.dumps(identity_data)),
    )
    # 3) org_members — preparer in irsyad (table gate; pairs with the app_meta claim above).
    cur.execute(
        """INSERT INTO org_members (id, org_id, user_id, role, accepted_at)
           VALUES (gen_random_uuid(), %s, %s, %s, now())""",
        (IRSYAD_ORG_ID, user_id, QA_ROLE),  # cols: (org_id, user_id, role)
    )


def show_structure(cur, user_id: str):
    """Print the resulting row structure for the fidelity confirmation (no pw)."""
    cur.execute("SELECT aud, role, email, (encrypted_password IS NOT NULL) has_pw, "
                "email_confirmed_at IS NOT NULL confirmed, raw_app_meta_data, raw_user_meta_data "
                "FROM auth.users WHERE id=%s", (user_id,))
    r = cur.fetchone()
    print("  auth.users:", {"aud": r[0], "role": r[1], "email": r[2], "has_pw": r[3],
                             "confirmed": r[4], "app_meta": r[5], "user_meta": r[6]})
    cur.execute("SELECT provider, provider_id=%s, identity_data FROM auth.identities WHERE user_id=%s",
                (user_id, user_id))
    r = cur.fetchone()
    print("  auth.identities:", {"provider": r[0], "provider_id_is_uid": r[1], "identity_data": r[2]})
    cur.execute("SELECT org_id, role, deleted_at FROM org_members WHERE user_id=%s", (user_id,))
    print("  org_members:", cur.fetchone())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wet-prove", action="store_true", help="BEGIN..ROLLBACK, throwaway pw, no keychain")
    ap.add_argument("--apply", action="store_true", help="REAL create + COMMIT (needs cai grant)")
    args = ap.parse_args()
    if args.wet_prove == args.apply:
        raise SystemExit("exactly one of --wet-prove / --apply")

    user_id = str(uuid.uuid4())
    conn = psycopg2.connect(_dsn())
    conn.autocommit = False
    cur = conn.cursor()
    try:
        if args.wet_prove:
            pw = "wetprove-throwaway-" + secrets.token_hex(8)  # NOT stored anywhere
            build(cur, user_id, pw)
            print(f"WET-PROVE ok (user_id={user_id}) — structure:")
            show_structure(cur, user_id)
            conn.rollback()
            print("ROLLED BACK — nothing written. Syntactic + structural validity proven.")
        else:
            pw = secrets.token_urlsafe(24)
            _store_keychain(pw)  # keychain FIRST, so we never create an account whose pw we lack
            build(cur, user_id, pw)
            show_structure(cur, user_id)
            conn.commit()
            print(f"APPLIED + COMMITTED — user_id={user_id}, email={QA_EMAIL}, role={QA_ROLE}")
            print(f"pw in Keychain: service={KEYCHAIN_SERVICE} account={KEYCHAIN_ACCOUNT}")
            print("NEXT: run the post-commit SIGN-IN TEST (CAI-1260 add'l 2) before calling it done.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
