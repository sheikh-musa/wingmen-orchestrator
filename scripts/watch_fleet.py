import os, time, sys, glob
import psycopg
from dotenv import load_dotenv
load_dotenv('/Users/sheikhmusa/wingmen/orchestrator/.env')
dsn=os.environ.get('SUPABASE_DB_URL') or os.environ.get('DATABASE_URL')
BASE=0
TGM='/Users/sheikhmusa/wingmen/orchestrator/logs/tg_media/'
deadline=time.time()+180*60
seen_shots=set(glob.glob(TGM+'*.png')+glob.glob(TGM+'*.jpg')+[p for p in glob.glob(TGM+'*.pdf') if '/_' not in p])
while time.time()<deadline:
    hits=[]
    newshots=[f for f in (glob.glob(TGM+'*.png')+glob.glob(TGM+'*.jpg')+[p for p in glob.glob(TGM+'*.pdf') if '/_' not in p]) if f not in seen_shots and os.path.getsize(f)>3000]
    if newshots: hits.append(("NEW-SCREENSHOTS", str(newshots), ""))
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute("select id,subject,left(body,350) from agent_messages where from_agent='cc-orchestrator' and to_agent='cc-orchestrator' and subject ilike '%%watchdog%%' and id>%s order by id desc limit 2",(BASE,))
        for r in cur.fetchall(): hits.append(("WATCHDOG-ESC #%s"%r[0],r[1],r[2]))
        cur.execute("select id,from_agent,subject,left(body,400) from agent_messages where (from_agent='cai' or from_agent='cc-reviewer') and to_agent in ('cc-orchestrator','cai') and id>%s order by id desc limit 2",(BASE,))
        for r in cur.fetchall(): hits.append(("%s #%s"%(r[1],r[0]),r[2],r[3]))
        cur.execute("""select id,from_agent,subject,left(body,300) from agent_messages where to_agent in ('cc-orchestrator','cai') and id>%s and from_agent like 'cc-%%' and from_agent not in ('cc-orchestrator','cc-reviewer') and (subject ilike '%%DELIVER%%' or subject ilike '%%DONE%%' or subject ilike '%%BLOCK%%' or subject ilike '%%grant%%' or subject ilike '%%estimate%%' or subject ilike '%%sweep%%' or subject ilike '%%merged%%' or subject ilike '%%shipped%%' or subject ilike '%%LIVE%%' or subject ilike '%%must-fix%%') order by id desc limit 3""",(BASE,))
        for r in cur.fetchall(): hits.append(("LANE #%s %s"%(r[0],r[1]),r[2],r[3]))
        cur.execute("select id,from_agent,subject,left(body,300) from agent_messages where to_agent='cc-orchestrator' and requires_response=true and id>%s and from_agent not in ('cc-orchestrator') order by id desc limit 3",(BASE,))
        for r in cur.fetchall(): hits.append(("RESP-NEEDED #%s %s"%(r[0],r[1]),r[2],r[3]))
        # DRAIN BACKSTOP (CAI-flagged 2x): surface undrained cai->cc-orch asks regardless of id, bounded 24h so ancient stale ones don't fire
        cur.execute("select id,from_agent,subject,left(body,300) from agent_messages where to_agent='cc-orchestrator' and from_agent='cai' and requires_response=true and responded_at is null and created_at > now() - interval '24 hours' order by id desc limit 3")
        for r in cur.fetchall(): hits.append(("UNDRAINED-CAI #%s"%r[0],r[2],r[3]))
        # HUB-BACKLOG BACKSTOP (operator-mandated 2026-06-22, "never slip again"): ANY lane's requires_response TO cc-orch
        # that I have not replied to yet (no later cc-orch->sender msg) — aging>20min so it's below the moving id-baseline,
        # bounded 24h to skip ancient. Catches dropped eyeballs/reviews/relays (the cosem PR#147 #3587 + shipforge #3631 slip).
        # Self-correcting: clears the instant I post any cc-orch->that-lane reply. NO manual stamping required.
        cur.execute("""
          select m.id, m.from_agent, m.subject, left(m.body,260),
                 round((extract(epoch from (now()-m.created_at))/3600.0)::numeric,1) as age_h
          from agent_messages m
          where m.to_agent='cc-orchestrator' and m.requires_response=true
            and m.from_agent <> 'cc-orchestrator'
            and m.created_at < now() - interval '20 minutes'
            and m.created_at > now() - interval '24 hours'
            and not exists (
              select 1 from agent_messages r
              where r.from_agent='cc-orchestrator' and r.to_agent=m.from_agent and r.id > m.id)
          order by m.created_at limit 6
        """)
        for r in cur.fetchall(): hits.append(("HUB-BACKLOG #%s %s (OWED %sh)"%(r[0],r[1],r[4]),r[2],r[3]))
        # DISPATCH-BACKLOG (operator "never slip", 2nd-instance fix 2026-06-23): MY requires_response asks TO a lane
        # that the lane hasn't replied to (no later lane->cc-orch msg), aged >2h, bounded 24h. Catches dispatches that
        # got dropped or routed to a non-live lane (the irsyad UAT ask sat ~3h on a dead lane; storefront self-closed on one).
        # Self-clears the instant that lane posts anything back to cc-orchestrator.
        cur.execute("""
          select m.id, m.to_agent, m.subject,
                 round((extract(epoch from (now()-m.created_at))/3600.0)::numeric,1) as age_h
          from agent_messages m
          where m.from_agent='cc-orchestrator' and m.requires_response=true
            and m.to_agent <> 'cc-orchestrator'
            and m.created_at < now() - interval '2 hours'
            and m.created_at > now() - interval '24 hours'
            and not exists (
              select 1 from agent_messages r
              where r.from_agent = m.to_agent and r.to_agent='cc-orchestrator' and r.id > m.id)
            -- suppress dispatches I'm ALREADY chasing: if cc-orch sent that lane any later message
            -- (a re-nudge / follow-up / park-note), I'm aware of it -> don't re-fire. Only surface
            -- GENUINELY-forgotten dispatches (no lane reply AND no cc-orch follow-up since).
            and not exists (
              select 1 from agent_messages f
              where f.from_agent='cc-orchestrator' and f.to_agent = m.to_agent and f.id > m.id)
          order by m.created_at limit 5
        """)
        for r in cur.fetchall(): hits.append(("DISPATCH-BACKLOG #%s ->%s (UNACK %sh)"%(r[0],r[1],r[3]), r[2], ""))
    if hits:
        # dedup by the leading tag's id so the same item isn't printed twice across queries
        seen=set(); uniq=[]
        for t in hits:
            key=t[0].split('#')[-1].split()[0] if '#' in t[0] else t[0]
            if key in seen: continue
            seen.add(key); uniq.append(t)
        for t in uniq: print("==",t[0],"=="); print("SUBJ:",t[1]); print("BODY:",t[2]); print()
        sys.exit(0)
    time.sleep(45)
print("TIMEOUT 180min (lull-cadence)")
sys.exit(0)
