// ihsanos-prod (multi-tenant) frozen non-reproducing row-id re-derivation (CAI-RESP-1364).
// Same mechanism as the goumlyne artifact, but PER-ORG: each org's audit_log chain is independent
// (prev_hash links within an org), so linkage is checked per-org and a frozen list is produced per-org.
// Node required (exact hashchain.ts canonicalPayloadJson). chain.jsonl carries payloads -> gitignored.
import { readFileSync, writeFileSync } from 'fs';
import { createHash } from 'crypto';
const canon = p => JSON.stringify(p, Object.keys(p).sort());
const computeHash = (prev, p) => createHash('sha256').update(prev + canon(p)).digest('hex');

const meta = JSON.parse(readFileSync(new URL('./snapshot_meta.json', import.meta.url)));
const rows = readFileSync(new URL('./chain.jsonl', import.meta.url), 'utf8').trim().split('\n').map(l => JSON.parse(l));

// group by org (rows already ordered by org_id, id)
const byOrg = new Map();
for (const e of rows) { if (!byOrg.has(e.org_id)) byOrg.set(e.org_id, []); byOrg.get(e.org_id).push(e); }

const perOrg = [];
for (const [org, es] of byOrg) {
  let linkageOk = true, firstBreak = null; const frozen = []; const cls = {};
  for (let i = 0; i < es.length; i++) {
    const e = es[i];
    if (i > 0 && e.prev_hash !== es[i - 1].hash) { linkageOk = false; if (firstBreak === null) firstBreak = e.id; }
    const k = e.entity_type + '/' + e.action; cls[k] = cls[k] || { r: 0, n: 0 };
    if (e.hash === computeHash(e.prev_hash, e.payload)) cls[k].r++;
    else { cls[k].n++; frozen.push(e.id); }
  }
  frozen.sort((a, b) => a - b);
  perOrg.push({
    org_id: org, rows: es.length, frozen_row_id_count: frozen.length,
    linkage_intact: linkageOk, first_linkage_break_id: firstBreak,
    frozen_row_ids: frozen,
    non_repro_classes: Object.fromEntries(Object.entries(cls).filter(([, v]) => v.n > 0).map(([k, v]) => [k, { reproduce: v.r, non_reproduce: v.n }])),
  });
}
perOrg.sort((a, b) => b.frozen_row_id_count - a.frozen_row_id_count);
const affected = perOrg.filter(o => o.frozen_row_id_count > 0);
const out = {
  decision_ref: 'CAI-RESP-1364', amnesty_mechanism: 'row-id-list',
  project_ref: meta.project_ref, silo: meta.silo, snapshot_at: meta.snapshot_at,
  tip_id: meta.tip_id, row_count_le_tip: meta.row_count_le_tip, distinct_orgs: meta.distinct_orgs,
  affected_org_count: affected.length,
  total_frozen_rows: affected.reduce((s, o) => s + o.frozen_row_id_count, 0),
  any_linkage_break: perOrg.some(o => !o.linkage_intact),
  per_org: perOrg,
};
writeFileSync(new URL('./frozen_amnesty_ihsanos_prod.json', import.meta.url), JSON.stringify(out, null, 2));
console.log('orgs total:', perOrg.length, '| affected (have non-repro):', affected.length);
console.log('total frozen rows:', out.total_frozen_rows, '| any linkage break:', out.any_linkage_break);
for (const o of affected) console.log(`  org ${o.org_id.slice(0,8)}: frozen=${o.frozen_row_id_count}/${o.rows} linkage_intact=${o.linkage_intact} classes=${Object.keys(o.non_repro_classes).join(',')}`);
