#!/usr/bin/env python3
"""fb_release_time.py <site> — print the latest Firebase Hosting release time (ISO Z).

Mints a token from the cosem service-account key (no gcloud). Used by deploy-watch
loops to detect when a new release lands. Prints empty on any failure.
"""
import json, os, sys, time, urllib.parse, urllib.request

try:
    import jwt
    site = sys.argv[1]
    sa = json.load(open(os.path.expanduser("~/.wingmen/keys/cosem-sa.json")))
    now = int(time.time())
    assertion = jwt.encode({"iss": sa["client_email"],
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "aud": sa["token_uri"], "iat": now, "exp": now + 3600},
        sa["private_key"], algorithm="RS256")
    data = urllib.parse.urlencode({"grant_type":
        "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
    tok = json.loads(urllib.request.urlopen(
        urllib.request.Request(sa["token_uri"], data=data)).read())["access_token"]
    req = urllib.request.Request(
        f"https://firebasehosting.googleapis.com/v1beta1/sites/{site}/releases?pageSize=1",
        headers={"Authorization": f"Bearer {tok}"})
    rels = json.loads(urllib.request.urlopen(req).read()).get("releases", [])
    print(rels[0].get("releaseTime", "")[:19] if rels else "")
except Exception:
    print("")
