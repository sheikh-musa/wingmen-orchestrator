import { classifyEnrollRows } from "@shared/lib/sch-bulk-enroll-shared";
type Roster = { student_number: string; display_name: string; class_id: string | null };
const FIRST = ["MUHAMMAD","NUR","SITI","AHMAD","NURUL","MUHAMMAD","MOHAMED","NURAIN","AISYAH","FATIMAH"];
const MID   = ["ALI","HASSAN","IBRAHIM","ISMAIL","YUSOF","RAHMAN","AZMAN","FAIZAL","HAKIM","DANISH","IRFAN","AIMAN"];
const LAST  = ["HASSAN","ISMAIL","RAHMAN","ABDULLAH","OMAR","SALLEH","BAKAR","HUSSEIN","MANSOR","KASSIM"];
const CLS = "class-1a";
function nm(f:number,m:number,l:number,con:string){return `${FIRST[f%FIRST.length]} ${MID[m%MID.length]} ${con} ${LAST[l%LAST.length]}`;}
// 889 roster — heavy shared tokens (MUHAMMAD/BIN/NUR/BINTE + small vocab)
const roster: Roster[] = [];
for (let i=0;i<889;i++){ const con = i%2? "BIN":"BINTE"; roster.push({student_number:`STU-${i}`, display_name: nm(i,i*7,i*13,con), class_id: CLS}); }
// 1147 input rows — force near-miss (non-exact) with heavy shared tokens: shift the LAST token
const rows = [];
for (let i=0;i<1147;i++){ const con=i%2?"BIN":"BINTE"; rows.push({key:i, display_name: nm(i,i*7,(i*13)+1,con), class_id: CLS}); }
const t0=performance.now();
const res = classifyEnrollRows(rows as any, roster as any);
const dt=performance.now()-t0;
const buckets: Record<string,number>={};
for(const r of res){buckets[r.bucket]=(buckets[r.bucket]??0)+1;}
console.log(`classifyEnrollRows(1147 rows vs 889 roster, heavy shared tokens): ${dt.toFixed(0)} ms`);
console.log("buckets:", buckets);
