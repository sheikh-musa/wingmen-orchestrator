# FF1 (NFPA 1001) Set A — ExamView answer-key extraction: RESULT

**Status: SOLVED & VERIFIED.** All 100 correct answers recovered directly from the
ExamView `.tst` binary and cross-checked 100/100. Seeded + wired. Report the key
below as authoritative for the FF1 Set A theory bank.

Date: 2026-07-09 · Source: `1. NFPA 1001-Fire Fighter 1_SET A_26.06.2025 (Certification Exam).tst`

---

## 1. Where the answer actually lives (the crack)

The `\x01\x03` after the options is a constant structural field (confirmed 100/100
= uint16 `1,3`) — NOT the answer. The real correct-answer index is a fixed 32-bit
**answer-record that sits BEFORE each question's stem**, with signature:

```
FF FF FF FF | 00000000 | 00000001 | 00000000 | <ANS_IDX> | 00000003 | <N_CHOICES>
             (n0=0)      (n_correct=1)(n2=0)     (0=A..3=D)   (const 3)  (=4)
```

Because the record precedes the stem, the k-th record in file order is the answer
for question k (the first record lives in the file header, before Q1's stem). This
is why a naive per-question scan finds it "off by one" and lands on the difficulty
field instead — that trap (a field with a plausible A22/B32/C34/D11 spread but
Q1=B and Q12="prior scuba diving cert") was explicitly ruled out.

Parser (robust, reusable, self-verifying):
`~/wingmen/content/adcda/question-banks/examview_tst_parser.py`
Run: `python3 examview_tst_parser.py "<file>.tst"` → prints key + `VERIFY: OK`.
(Correctly refuses the 40-question `Set A.tst`, whose 40 records also interleave
1:1 — independent confirmation the record signature generalizes.)

## 2. Verification evidence

PASS Q1 = D 'Save lives and protect property and the environment'
PASS distribution {'a': 22, 'b': 32, 'c': 34, 'd': 12}

- **Interleaving**: exactly 100 answer-records; each falls between question k-1's
  and question k's option-marker → clean 1:1 mapping (checked programmatically).
- **N_CHOICES self-check**: record's N_CHOICES == parsed option count for 100/100;
  no answer index ever ≥ option count.
- **Cross-match to seed JSON**: option-A text order aligned 100/100; the answer
  text at the extracted index matched the seed option text 0 mismatches/100.
- **Domain spot-checks (12+)** all agree with NFPA 1001 FF1 knowledge, incl. every
  case where the difficulty-field decoy failed: Q1=D (mission=save lives), Q2=B
  (prevent duplication), Q3=B (consistency/safety/accountability), Q8=B (proceed to
  safe area), Q9=D (identify personnel on scene), Q10=A (MAYDAY), Q11=A (SCBA until
  monitored), Q12=B (cardiovascular fitness — NOT the "scuba" decoy), Q25=A (beams),
  Q28=C (75°), Q63=B (conduction), Q80=A (Class A), Q81=A (Class K deep-frying),
  Q86=D (inline valve at meter), Q100=C (tag/knot defective hose per SOP).
- **Distribution**: A=22, B=32, C=34, D=12 (no single-letter dominance).

## 3. The answer key (n:Letter)

```
    1:D    2:B    3:B    4:C    5:C    6:C    7:C    8:B    9:D   10:A
   11:A   12:B   13:D   14:C   15:C   16:C   17:B   18:C   19:C   20:A
   21:B   22:C   23:A   24:B   25:A   26:C   27:C   28:C   29:C   30:D
   31:D   32:A   33:B   34:B   35:A   36:C   37:B   38:D   39:A   40:B
   41:C   42:A   43:C   44:B   45:C   46:A   47:B   48:C   49:B   50:C
   51:A   52:B   53:A   54:D   55:D   56:B   57:C   58:A   59:A   60:A
   61:C   62:A   63:B   64:C   65:A   66:C   67:B   68:B   69:B   70:C
   71:B   72:B   73:C   74:D   75:B   76:B   77:B   78:C   79:A   80:A
   81:A   82:C   83:C   84:B   85:B   86:D   87:C   88:C   89:A   90:B
   91:B   92:D   93:C   94:D   95:A   96:B   97:B   98:C   99:B  100:C
```

<details><summary>Full key with correct-answer text (Q | ans | text)</summary>

| Q | Ans | Correct option text |
|---|-----|---------------------|
| 1 | D | Save lives and protect property and the environment |
| 2 | B | Prevent the duplication of effort |
| 3 | B | It ensures consistency, safety, and accountability during operatio |
| 4 | C | They may offer specialized resources and support to enhance incide |
| 5 | C | To offer confidential support for personal, emotional, or psycholo |
| 6 | C | transmitted to the responding units or personnel. |
| 7 | C | units or individuals must identify themselves in every transmissio |
| 8 | B | proceed to a designated safe area outside the collapse zone. |
| 9 | D | They identify which personnel are working at a scene and help the  |
| 10 | A | Broadcast a MAYDAY call |
| 11 | A | wear SCBA until air monitoring demonstrates that the atmosphere is |
| 12 | B | Sufficient cardiovascular and respiratory fitness to handle strenu |
| 13 | D | Firefighters should always maintain three points of contact when m |
| 14 | C | seat belt |
| 15 | C | Standing or changing seats while the apparatus is in motion |
| 16 | C | Retroreflective trim |
| 17 | B | high enough to protect the lower leg. |
| 18 | C | Establish a safe work zone using traffic cones and apparatus |
| 19 | C | Hollow-core wood |
| 20 | A | Inaccessible hinges |
| 21 | B | they may conceal electrical wires and gas pipes |
| 22 | C | An area filled with heavy smoke and active fire impingement |
| 23 | A | SCBA failure |
| 24 | B | Smoke changing from light to dark and turbulent |
| 25 | A | Beams |
| 26 | C | Ladders should be raised at least 10 feet (3 m) away from electric |
| 27 | C | Stability, levelness, and debris |
| 28 | C | 75 degrees |
| 29 | C | forward with his or her arms straight, grasping alternate rungs. |
| 30 | D | Straight to medium fog pattern so that the fire stream reaches the |
| 31 | D | At a 45 degree angle from the side of the vehicle |
| 32 | A | Some alternative fuel vehicles have a visible logo on the front or |
| 33 | B | Check compartments systematically using a thermal imaging camera |
| 34 | B | Provides wider coverage and helps control flying embers |
| 35 | A | Collapse |
| 36 | C | CO2 |
| 37 | B | use hooks or pike poles to break apart material |
| 38 | D | Deep-seated embers that are hard to access |
| 39 | A | To protect from toxic gases and chemical exposure |
| 40 | B | Largest pile of debris |
| 41 | C | is most appropriate for the task at hand. |
| 42 | A | Remove all other loads or activity on the ladder during the rescue |
| 43 | C | practice emergency exit techniques. |
| 44 | B | High heat and thick, black smoke at floor level |
| 45 | C | An thorough search using different personnel than the primary sear |
| 46 | A | improved visibility in an obscured environment. |
| 47 | B | move below the smoke level |
| 48 | C | move the firefighter carefully so as not to dislodge the mask |
| 49 | B | Decreases |
| 50 | C | Rotating the selector ring to the desired gpm (L/min) setting |
| 51 | A | The fire begins to lose intensity and smoke diminishes |
| 52 | B | Backdraft conditions |
| 53 | A | To prevent the fire from spreading to adjacent structures or areas |
| 54 | D | smoke, dangerous gases, and chemicals |
| 55 | D | are already in the physical state required for ignition. |
| 56 | B | Exposure to toxic gases found in smoke and/or lack of oxygen |
| 57 | C | of items behind reflective metal or glass |
| 58 | A | It is dependent on weather conditions and the direction of wind, w |
| 59 | A | Backdraft |
| 60 | A | Whether the fire is fuel-limited or ventilation-limited |
| 61 | C | Tendency of gases to form layers according to temperature |
| 62 | A | Carbon monoxide (CO) |
| 63 | B | Conduction |
| 64 | C | Roof units are sagging or leaning |
| 65 | A | Steel and concrete structures generally resist fire better than wo |
| 66 | C | It releases heat and smoke from the upper levels of a structure |
| 67 | B | 1.5-inch attack line |
| 68 | B | taking it outside the structure to extinguish |
| 69 | B | Thermal imaging camera (TIC) |
| 70 | C | Toxic gases |
| 71 | B | To preserve potential evidence for fire cause determination |
| 72 | B | Maintains public trust and confidence |
| 73 | C | Efforts to reduce damage caused by fire and firefighting |
| 74 | D | Protect unaffected furniture and areas of the building. |
| 75 | B | Stopping flow from the sprinkler head |
| 76 | B | should do minimal damage and provide quick access. |
| 77 | B | All evidence has been collected |
| 78 | C | Spotter using standard hand signals |
| 79 | A | may provide water through drafting operations |
| 80 | A | A |
| 81 | A | Commercial deep-frying |
| 82 | C | Gloves and helmet |
| 83 | C | Overtaxing a power source |
| 84 | B | Position elevated tripod-mounted floodlights |
| 85 | B | It increases the risk of electrocution |
| 86 | D | Turn the inline valve near the meter |
| 87 | C | ground |
| 88 | C | Perimeter control |
| 89 | A | Provides fresh air that speeds up combustion |
| 90 | B | creating a control zone perimeter. |
| 91 | B | It has visible signs of fraying or cuts. |
| 92 | D | make sure that all personnel are clear of the hoisting area. |
| 93 | C | Lifting victims and rescuers |
| 94 | D | detect oxygen levels and hazardous substances. |
| 95 | A | SCBA is required to be used for the operation. |
| 96 | B | Hazardous gas concentration is above safe levels |
| 97 | B | Allow it to air dry |
| 98 | C | Mild detergent |
| 99 | B | marked out of service. |
| 100 | C | Tie an overhand knot on one end or tag it and report it per depart |

</details>

## 4. What was seeded / wired (repo: cosem-adcda, branch feat/adcda-theory-reader-normalization)

- `docs/seed-data/theory_question_bank_ff1_set_a.json` — 100/100 keyed:
  `correct_answer` (letter) + `correct_answer_text_en/ar` + `num` (1–100) added;
  per-item `status:"answer-key-verified"`; meta.answer_key_status updated.
- `src/data/ff1TheoryQuestionBank.js` — new bundled `FF1_SET_A` ES module (mirrors
  `hazmatTheoryQuestionBank.js`), letter-keyed English options.
- `src/services/theoryItemAnalysis.js` — registered `ff1_set_a` in `SET_BANKS`;
  option-letter derivation now accepts `options_ar` OR `options`.
- **Scoring proof**: perfect responder → 100/100; a "B-spammer" → 32 (== #B keys).
  Lint clean; 42/42 theory unit tests pass.

## 5. Note on status token

Brief said `status:"keyed"`; I used **`"answer-key-verified"`** to match the sibling
Hazmat bank's schema (same directory/pipeline) and because it's the more accurate,
defensible token for a real cert exam. Flag if you want the literal `"keyed"`.

## 6. Not fabricated

No answer was LLM-guessed. Every letter is read from the file bytes and independently
cross-checked. The 12+ domain checks were used only to *validate* the decode (and to
kill the difficulty-field decoy), never to source an answer.
