import { readFileSync } from 'fs';
const rows = readFileSync(new URL('./chain.jsonl', import.meta.url),'utf8').trim().split('\n').map(l=>JSON.parse(l));
const byOrg = new Map();
for (const e of rows){ if(!byOrg.has(e.org_id)) byOrg.set(e.org_id,[]); byOrg.get(e.org_id).push(e); }
for (const [org, es] of byOrg){
  const breaks=[];
  for(let i=1;i<es.length;i++){ if(es[i].prev_hash!==es[i-1].hash) breaks.push(i); }
  if(!breaks.length) continue;
  // build hash->row index to detect forks (prev_hash points to an EARLIER row = concurrent-write fork)
  const hashAt = new Map(); es.forEach((e,i)=>hashAt.set(e.hash,i));
  console.log(`\n=== org ${org} : ${es.length} rows, ${breaks.length} linkage break(s) ===`);
  console.log(`  id range ${es[0].id}..${es[es.length-1].id}, first prev_hash=${(es[0].prev_hash||'').slice(0,12)} (genesis? ${es[0].prev_hash==='genesis'})`);
  for(const i of breaks.slice(0,6)){
    const e=es[i], p=es[i-1];
    const pointsTo = hashAt.has(e.prev_hash) ? `row idx ${hashAt.get(e.prev_hash)} (id ${es[hashAt.get(e.prev_hash)].id})` : 'NO row in this org (deletion/foreign/genesis?)';
    console.log(`  break at idx ${i} id=${e.id} ${e.entity_type}/${e.action}: prev_hash=${(e.prev_hash||'').slice(0,12)} -> ${pointsTo}; prior row id=${p.id} hash=${(p.hash||'').slice(0,12)}`);
  }
  if(breaks.length>6) console.log(`  ...(${breaks.length-6} more)`);
}
