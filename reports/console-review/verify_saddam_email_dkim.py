#!/usr/bin/env python3
"""
Chain-of-custody verifier for the zakat-flip authority email (CAI-1238/1239).

Verifies a DOWNLOAD-ORIGINAL .eml is a genuine, unforwarded email from
saddam@irsyad.edu.sg, by GENERATING a fresh cryptographic DKIM result
(live DNS), NOT by reading any claimed Authentication-Results header.

Meets cai CAI-1239's 3 corrections:
  1. LIVE check: dkim.verify() re-derives the result against live DNS now.
  2. d=irsyad.edu.sg SPECIFICALLY (not just "a pass") + From is signed.
  3. TRUE RAW ORIGINAL only: a Forward/paste breaks the sig -> verify() False (fail-closed).

Usage:  python3 verify_saddam_email.py /path/to/original.eml
        python3 verify_saddam_email.py --selftest
"""
import sys, re
from email import message_from_bytes
from email.utils import parseaddr

EXPECT_DOMAIN = "irsyad.edu.sg"
EXPECT_FROM   = "saddam@irsyad.edu.sg"


def parse_dkim_sig(raw_bytes):
    """Return list of (tag->value) dicts for each DKIM-Signature header."""
    msg = message_from_bytes(raw_bytes)
    sigs = []
    for hv in msg.get_all("DKIM-Signature", []):
        flat = re.sub(r"\s+", "", hv)
        tags = {}
        for part in flat.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                tags[k] = v
        sigs.append(tags)
    return msg, sigs


def verify(path):
    raw = open(path, "rb").read()
    msg, sigs = parse_dkim_sig(raw)

    from_hdr = msg.get("From", "")
    from_addr = parseaddr(from_hdr)[1].lower()

    print(f"From: {from_hdr!r}  -> addr={from_addr}")
    print(f"DKIM-Signature headers found: {len(sigs)}")
    for i, s in enumerate(sigs):
        print(f"  sig[{i}] d={s.get('d')} s={s.get('s')} h={s.get('h','')[:80]}")

    checks = {}

    # (2a) at least one signature for EXACTLY irsyad.edu.sg
    irsyad_sigs = [s for s in sigs if (s.get("d", "").lower() == EXPECT_DOMAIN)]
    checks["signing_domain_is_irsyad"] = bool(irsyad_sigs)

    # (2b) that signature must cover the From header
    def covers_from(s):
        h = s.get("h", "").lower()
        return "from" in [x.strip() for x in h.split(":")]
    checks["irsyad_sig_covers_from"] = any(covers_from(s) for s in irsyad_sigs)

    # (2c) From address is Saddam's
    checks["from_is_saddam"] = (from_addr == EXPECT_FROM)

    # (1)+(3) LIVE cryptographic verification against live DNS on the raw original
    try:
        import dkim
        # verify() returns True only if a signature validates. We additionally
        # require that the *validating* signature is the irsyad.edu.sg one by
        # checking each signature index.
        ok_any = dkim.verify(raw)
        checks["dkim_verify_true"] = bool(ok_any)
        # per-signature: confirm the irsyad.edu.sg sig specifically validates
        d = dkim.DKIM(raw)
        irsyad_idx_ok = False
        for idx, s in enumerate(sigs):
            if s.get("d", "").lower() == EXPECT_DOMAIN:
                try:
                    if d.verify(idx=idx):
                        irsyad_idx_ok = True
                except Exception as e:
                    print(f"  sig[{idx}] verify error: {e}")
        checks["irsyad_sig_validates"] = irsyad_idx_ok
    except Exception as e:
        print(f"DKIM verify EXCEPTION (treat as FAIL): {e!r}")
        checks["dkim_verify_true"] = False
        checks["irsyad_sig_validates"] = False

    print("\n--- CHECKS ---")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")

    # Overall: the irsyad.edu.sg signature must cryptographically validate,
    # cover From, and From must be Saddam.
    verdict = (checks.get("irsyad_sig_validates")
               and checks.get("irsyad_sig_covers_from")
               and checks.get("from_is_saddam"))
    print(f"\n=== VERDICT: {'CHAIN-OF-CUSTODY VERIFIED (flip-clear)' if verdict else 'FAIL — DO NOT FLIP'} ===")
    return verdict


def selftest():
    # Fail-closed behavior on non-signed / garbage input.
    import tempfile, os
    print("### SELFTEST 1: garbage input must FAIL (fail-closed) ###")
    fd, p = tempfile.mkstemp(suffix=".eml")
    os.write(fd, b"From: saddam@irsyad.edu.sg\r\nSubject: x\r\n\r\nno dkim here\r\n")
    os.close(fd)
    v = verify(p)
    os.unlink(p)
    assert v is False, "garbage/unsigned must FAIL"
    print("\n### SELFTEST 2: forged plain-text 'dkim=pass' Authentication-Results must NOT pass ###")
    fd, p = tempfile.mkstemp(suffix=".eml")
    os.write(fd, (b"Authentication-Results: mx.google.com; dkim=pass header.d=irsyad.edu.sg\r\n"
                  b"DKIM-Signature: v=1; a=rsa-sha256; d=irsyad.edu.sg; s=fake; h=from:subject;\r\n"
                  b" bh=AAAA; b=FAKESIGNATUREdata\r\n"
                  b"From: saddam@irsyad.edu.sg\r\nSubject: x\r\n\r\nbody\r\n"))
    os.close(fd)
    v = verify(p)
    os.unlink(p)
    assert v is False, "forged/invalid signature must FAIL despite a claimed dkim=pass line"
    print("\nSELFTEST OK: verifier is fail-closed; a claimed 'dkim=pass' header does NOT pass without a valid signature.")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) == 2:
        sys.exit(0 if verify(sys.argv[1]) else 1)
    else:
        print(__doc__)
        sys.exit(2)
