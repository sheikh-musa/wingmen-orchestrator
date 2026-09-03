// Re-derive the FROZEN non-reproducing audit_log row-id list for the amnesty (CAI-RESP-1363).
// INDEPENDENTLY RE-RUNNABLE: (1) python rederive_dump.py re-dumps goumlyne_chain.jsonl + snapshot_meta.json
// from GOUMLYNE_RO_DATABASE_URL at the current tip; (2) this script recomputes, via the EXACT
// src/shared/lib/hashchain.ts logic, which rows do NOT content-reproduce. Those ids are the frozen amnesty set.
// A row is amnestied iff its id is on this frozen list — NOT by (entity_type,action) class (CAI-RESP-1363:
// row-id list closes the mixed-class masking gap; a currently-reproducing app row is never on the list, so a
// tamper on it still reads BROKEN). Node is REQUIRED (JSON.stringify with an array replacer drops nested keys
// at every level — a python sort_keys approximation would false-flag nested payloads).
import { readFileSync, writeFileSync } from 'fs';
import { createHash } from 'crypto';

function canonicalPayloadJson(payload){ return JSON.stringify(payload, Object.keys(payload).sort()); } // hashchain.ts:11
function computeHash(prev, payload){ return createHash('sha256').update(prev + canonicalPayloadJson(payload)).digest('hex'); } // hashchain.ts:19

const meta = JSON.parse(readFileSync(new URL('./snapshot_meta.json', import.meta.url)));
const entries = readFileSync(new URL('./goumlyne_chain.jsonl', import.meta.url),'utf8').trim().split('\n').map(l=>JSON.parse(l));

let linkageOk = true, firstLinkageBreak = null;
const frozen = [];                 // ids that do NOT content-reproduce
const byClass = {};                // (entity_type/action) -> {repro, nonrepro}
for (let i=0;i<entries.length;i++){
  const e = entries[i];
  if (i>0 && e.prev_hash !== entries[i-1].hash){ linkageOk=false; if(firstLinkageBreak===null) firstLinkageBreak=e.id; }
  const k = e.entity_type+'/'+e.action;
  byClass[k] = byClass[k] || {repro:0, nonrepro:0};
  if (e.hash === computeHash(e.prev_hash, e.payload)) byClass[k].repro++;
  else { byClass[k].nonrepro++; frozen.push(e.id); }
}
frozen.sort((a,b)=>a-b);
const out = {
  decision_ref: "CAI-RESP-1363",
  amnesty_mechanism: "row-id-list",
  org_id: meta.org_id, project_ref: meta.project_ref,
  snapshot_at: meta.snapshot_at, tip_id: meta.tip_id, tip_hash: meta.tip_hash,
  row_count_le_tip: meta.row_count_le_tip,
  frozen_row_id_count: frozen.length,
  linkage_intact: linkageOk, first_linkage_break_id: firstLinkageBreak,
  frozen_row_ids: frozen,
  documentary_classes: Object.fromEntries(Object.entries(byClass).filter(([,v])=>v.nonrepro>0)
     .map(([k,v])=>[k,{reproduce:v.repro, non_reproduce:v.nonrepro}])),
};
writeFileSync(new URL('./frozen_amnesty_goumlyne.json', import.meta.url), JSON.stringify(out,null,2));
console.log("linkage_intact:", linkageOk, "first_break:", firstLinkageBreak);
console.log("rows:", entries.length, "frozen(non-reproducing):", frozen.length);
console.log("documentary classes (non-repro present):", JSON.stringify(out.documentary_classes));
console.log("frozen id range:", frozen[0], "..", frozen[frozen.length-1]);
console.log("wrote frozen_amnesty_goumlyne.json");
