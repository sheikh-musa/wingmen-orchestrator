import json, os
sp = os.path.dirname(__file__)
d = json.load(open(os.path.join(sp,"raw.json")))
C = d["ceayj"]; G = d["goumlyne"]

def err(x): return isinstance(x, dict) and "__error__" in x
for k in C:
    if err(C[k]) or err(G[k]):
        print("ERROR", k, C[k] if err(C[k]) else "", G[k] if err(G[k]) else "")

MONEY = ["pos_order","donation","donor","tabung","organization","org_role_permission","payment","wallet","ledger","transaction"]
def is_money(t): return any(m in t.lower() for m in MONEY)

# ---- Tables ----
ct = {r["table_name"] for r in C["tables"]}
gt = {r["table_name"] for r in G["tables"]}
shared = sorted(ct & gt)
print("=== TABLES ===")
print("ceayj total:", len(ct), "goumlyne total:", len(gt), "shared:", len(shared))
print("ceayj-only:", sorted(ct-gt))
print("goumlyne-only:", sorted(gt-ct))

# ---- Columns ----
def colmap(rows):
    m={}
    for r in rows:
        m.setdefault(r["table_name"],{})[r["column_name"]]=(r["data_type"],r["is_nullable"])
    return m
Cc=colmap(C["columns"]); Gc=colmap(G["columns"])
print("\n=== COLUMN DRIFT (shared tables) ===")
for t in shared:
    cc=Cc.get(t,{}); gc=Gc.get(t,{})
    only_c=set(cc)-set(gc); only_g=set(gc)-set(cc)
    typdiff=[(col,cc[col],gc[col]) for col in set(cc)&set(gc) if cc[col]!=gc[col]]
    if only_c or only_g or typdiff if False else (only_c or only_g or typdiff):
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"\n-- {tag}{t}")
        for col in sorted(only_c): print(f"   ceayj-only col: {col} {cc[col]}")
        for col in sorted(only_g): print(f"   goum-only  col: {col} {gc[col]}")
        for col,a,b in typdiff: print(f"   TYPE/NULL differ: {col} ceayj={a} goum={b}")

# ---- Indexes ----
def idxmap(rows):
    m={}
    for r in rows:
        m.setdefault(r["tablename"],{})[r["indexname"]]=r["indexdef"]
    return m
Ci=idxmap(C["indexes"]); Gi=idxmap(G["indexes"])
print("\n=== INDEX DRIFT (shared tables) ===")
for t in shared:
    ci=Ci.get(t,{}); gi=Gi.get(t,{})
    only_c=set(ci)-set(gi); only_g=set(gi)-set(ci)
    # normalize def by stripping index name to compare structure
    defdiff=[]
    for n in set(ci)&set(gi):
        if ci[n]!=gi[n]: defdiff.append((n,ci[n],gi[n]))
    if only_c or only_g or defdiff:
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"\n-- {tag}{t}")
        for n in sorted(only_c): print(f"   ceayj-only idx: {ci[n]}")
        for n in sorted(only_g): print(f"   goum-only  idx: {gi[n]}")
        for n,a,b in defdiff: print(f"   DEF differ {n}:\n      ceayj={a}\n      goum ={b}")

# ---- Policies ----
def polmap(rows):
    m={}
    for r in rows:
        key=r["policyname"]
        m.setdefault(r["tablename"],{})[key]={
            "cmd":r["cmd"],"roles":r["roles"],"qual":r["qual"],"with_check":r["with_check"],"permissive":r["permissive"]}
    return m
Cp=polmap(C["policies"]); Gp=polmap(G["policies"])
print("\n=== RLS POLICY DRIFT (shared tables) ===")
alltab=sorted(set(Cp)|set(Gp))
for t in alltab:
    if t not in shared: continue
    cp=Cp.get(t,{}); gp=Gp.get(t,{})
    only_c=set(cp)-set(gp); only_g=set(gp)-set(cp)
    valdiff=[(n,cp[n],gp[n]) for n in set(cp)&set(gp) if cp[n]!=gp[n]]
    if only_c or only_g or valdiff:
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"\n-- {tag}{t}")
        for n in sorted(only_c): print(f"   ceayj-only policy: {n} {cp[n]}")
        for n in sorted(only_g): print(f"   goum-only  policy: {n} {gp[n]}")
        for n,a,b in valdiff: print(f"   policy differ {n}:\n      ceayj={a}\n      goum ={b}")

# tables with RLS policies in one silo but table has zero policies in other
print("\n   [policy-count summary for shared tables where counts differ]")
for t in shared:
    nc=len(Cp.get(t,{})); ng=len(Gp.get(t,{}))
    if nc!=ng:
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"   {tag}{t}: ceayj={nc} policies, goum={ng} policies")

# ---- Grants ----
def tgmap(rows):
    m={}
    for r in rows:
        m.setdefault(r["table_name"],set()).add((r["grantee"],r["privilege_type"]))
    return m
Ctg=tgmap(C["table_grants"]); Gtg=tgmap(G["table_grants"])
print("\n=== TABLE GRANT DRIFT (anon/authenticated, shared tables) ===")
for t in shared:
    cg=Ctg.get(t,set()); gg=Gtg.get(t,set())
    only_c=cg-gg; only_g=gg-cg
    if only_c or only_g:
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"\n-- {tag}{t}")
        for x in sorted(only_c): print(f"   ceayj-only grant: {x}")
        for x in sorted(only_g): print(f"   goum-only  grant: {x}")

def cgmap(rows):
    m={}
    for r in rows:
        m.setdefault(r["table_name"],set()).add((r["column_name"],r["grantee"],r["privilege_type"]))
    return m
Ccg=cgmap(C["col_grants"]); Gcg=cgmap(G["col_grants"])
print("\n=== COLUMN GRANT DRIFT (anon/authenticated, shared tables) ===")
for t in shared:
    cg=Ccg.get(t,set()); gg=Gcg.get(t,set())
    only_c=cg-gg; only_g=gg-cg
    # filter out those already implied by table-level or missing columns; still report
    if only_c or only_g:
        tag="[MONEY/PII] " if is_money(t) else ""
        print(f"\n-- {tag}{t}: ceayj-only={len(only_c)} goum-only={len(only_g)}")
        for x in sorted(only_c)[:40]: print(f"   ceayj-only cgrant: {x}")
        for x in sorted(only_g)[:40]: print(f"   goum-only  cgrant: {x}")

# ---- Functions ----
def fnmap(rows):
    m={}
    for r in rows:
        m.setdefault(r["routine_name"],[]).append((r.get("args"),r.get("secdef")))
    return m
Cf=fnmap(C["routines"]); Gf=fnmap(G["routines"])
print("\n=== FUNCTION DRIFT (public) ===")
print("ceayj fns:", len(Cf), "goum fns:", len(Gf))
only_c=set(Cf)-set(Gf); only_g=set(Gf)-set(Cf)
for n in sorted(only_c):
    sd=any(s for _,s in Cf[n])
    print(f"   ceayj-only fn: {n}  SECDEF={sd}")
for n in sorted(only_g):
    sd=any(s for _,s in Gf[n])
    print(f"   goum-only  fn: {n}  SECDEF={sd}")
# secdef flag differences on shared fns
print("   -- SECDEF flag differences on shared fns --")
for n in sorted(set(Cf)&set(Gf)):
    csd=any(s for _,s in Cf[n]); gsd=any(s for _,s in Gf[n])
    if csd!=gsd:
        print(f"   {n}: ceayj SECDEF={csd} goum SECDEF={gsd}")
