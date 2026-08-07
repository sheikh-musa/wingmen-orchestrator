# EXTRACT FF1 answer key from the ExamView .tst — findings + task

Operator gave us the ExamView source. The FF1 answer key IS recoverable — the .tst is NOT encrypted, just zlib-compressed. Do NOT LLM-guess answers (real SCDF cert exam) — read them out of the file + verify.

## Files
- `~/wingmen/content/adcda/question-banks/1. NFPA 1001-Fire Fighter 1_SET A_26.06.2025 (Certification Exam).tst` (21,475 bytes)
- `~/wingmen/content/adcda/question-banks/Set A.tst` (11,602 bytes) — smaller, may be the same test w/o media; cross-check.
- Target to seed: `~/wingmen/projects/cosem-adcda/docs/seed-data/theory_question_bank_ff1_set_a.json` (100 Qs, all `correct_answer:"None"`, `status:"answer-key-pending"`).

## Format cracked so far (orch reverse-engineering)
- 16-byte header `\x1aFSCTST#W#06#00#` (ExamView "FSCTST" format v06), then a **zlib stream at offset 16**. `zlib.decompress(open(f,'rb').read()[16:])` → ~215,680 bytes.
- Decompressed body = ExamView binary with **UTF-16LE** text. Question layout: `<qtext> \x10a.\x11<optA> \x11\x11b.\x11<optB> \x11\x11c.\x11<optC> \x11\x11d.\x11<optD> \x11\x11 ...`
- `re.finditer('\x10a\\.\x11', u)` finds exactly **100 MC questions**. Options extract cleanly.
- **ANSWER KEY LOCATION = the open problem.** The `\x01\x03` immediately after the options is a CONSTANT structural field (98/100 = 0x03), NOT the answer — do not use it. The correct-answer field is elsewhere: likely (a) a separate answer-record/answer-key section later in the decompressed body, or (b) a per-question record field at a different offset. Map index→letter 0=A..4=E.

## Task
1. Write a robust ExamView `.tst` parser (Python). Extract all 100 questions + their **correct answers** from the real file bytes.
2. VERIFY before trusting: (a) Q1 "overall mission of the fire service" must resolve to **D** ("Save lives and protect property and the environment"); (b) sane answer distribution (NOT 98% one letter); (c) spot-check ~5 against known NFPA 1001 FF1 answers. If unsure, flag for the operator to export the ExamView "Answer Key" (File→Export/Print→Answer Key) as ground truth.
3. Seed the 100 answers into `theory_question_bank_ff1_set_a.json` (`correct_answer` + `status:"keyed"`), and wire into the SCDF theory scoring so the demo theory quiz scores correctly.
4. Report the extracted key + your verification evidence to cc-orchestrator before it's treated as authoritative.
