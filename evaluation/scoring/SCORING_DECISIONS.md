# Scoring record and author decisions (supplementary material)

## What was fixed before the run

| Item | Fixed | Changed afterwards |
|---|---|---|
| Test bank (100 questions) | 2026-10-30 09:07 (Beijing), SHA-256 `2de53d60…` | no |
| Reference values (independent computation) | same freeze, SHA-256 `800992e5…` | no |
| Mapping of everyday wording to indicators | same freeze, SHA-256 `09427fe5…` | no |
| Written review rules | before the first run | no; borderline cases decided by the author (below) |
| System code and configuration | sealed and fingerprinted before each run | no |
| Model outputs | recorded once per run | no; nothing was re-run to change a verdict |

Answers lost to infrastructure faults were retested once in a separate run and are counted as failures in the main results.

## Principles used for borderline cases (Appendix B), applied to every system

1. Requirements follow the question and the definition of its intent: an open question requires the profile elements; a position question requires the rank or the gap to the benchmark, not the dimension breakdown.
2. A clarification is accepted when the wording is genuinely ambiguous and the question offers the reading the reference expects.
3. What the page shows counts, not what the system computed. A requested chart must be an actual chart image; text bars or unrendered chart code do not count. The strongest and weakest dimensions count as given when the six dimension scores are shown.

## Decisions on the reported run (run 1)

| Date (Beijing) | Question | System(s) | Verdict | Reason |
|---|---|---|---|---|
| 2026-10-31 | X29 | EduTA, Direct-SQL | CLARIFY_OK | bare "Jilin" may be the province or the city (principle 2) |
| 2026-10-31 | X01 | No-correction | FAIL | population restricted by a misread school-stage filter (principle 3) |
| 2026-10-31 | Y03 | No-correction | FAIL | number of valid schools stated as "no data" (principle 3) |
| 2026-10-31 | X43 | Rule-based | FAIL | normalised score shown where a count was asked (principle 3) |
| 2026-11-02 | Y23 | No-correction | PASS → FAIL | empty plot where two bars were requested; found when all answers were re-reviewed with systems masked (principle 3) |
| 2026-11-02 | X06 | EduTA, No-correction | PASS → FAIL | dimension breakdown missing |
| 2026-11-07 | X06 | EduTA, No-correction | FAIL → PASS | position question: rank and gap to the national mean suffice (principle 1); the other systems still fail X06 on wrong numbers or ranks |
| 2026-11-07 | X04 | all | unchanged | position question; existing verdicts already consistent with principle 1 |
| 2026-11-07 | Y35 | all | unchanged | the frozen mapping (resource-use sub-dimension) is kept |
| 2026-11-07 | X56 | Tool-calling | FAIL | lists both districts named Putuo without asking which one is meant |

Resulting counts of the reported run: EduTA 86, No-correction 79, Rule-based 51, Tool-calling 73, Direct-SQL 52, MAC-SQL 42.
Effect of the decisions on EduTA / No-correction: 86 / 80 after 2026-10-31, 85 / 78 after 2026-11-02, 86 / 79 after 2026-11-07.

## Decisions on the repeated runs (2026-11-08)

| Run | Question | System | Verdict | Reason |
|---|---|---|---|---|
| 2 | Y02, Y03, Y07, Y12, Y15 | Tool-calling | FAIL | text bars are not a chart (principle 3); one reviewer and a second, independent re-review had accepted them; both reviews are kept |
| 3 | Y36, Y39 | Tool-calling | PASS | additional figures are present in the tool output |
| 3 | X01 | EduTA | PASS | strongest and weakest dimensions readable from the six scores shown (principle 3); no other answer of any system failed on this point alone |

Resulting counts: EduTA 86 / 86 / 86, No-correction 79 / 81 / 80, Tool-calling 73 / 79 / 78.

## Where the evidence is

Question-level verdicts, reviewer reasons and the masked review packets are released with the code. The external expert's verdicts and the agreement analysis are provided alongside them.

## Sensitivity of the main comparisons to the author decisions (run 1)

With the reviewers' original verdicts instead of the decisions above: EduTA 84 (86 signed), No-correction 79 (79), Rule-based 52 (51), Tool-calling 73 (73), Direct-SQL 51 (52), MAC-SQL 42 (42). Sign tests against EduTA: No-correction p = 0.36 (0.19), Tool-calling p = 0.09 (0.035); Rule-based, Direct-SQL and MAC-SQL p < 0.001 in both.

## Items treated as not collected (rule R11)

No school gave a non-zero answer to any of these items in the year concerned.

| Year | Items | Item numbers |
|---|---|---|
| 2020 | 38 | 46–83 |
| 2021 | 35 | 49–83 |
| 2022 | 40 | 3, 5, 8, 9, 11, 14, 15, 17, 23, 25, 26, 45–48, 59–83 |
| 2023 | 28 | 1, 3, 5–8, 11, 12, 14–17, 23–27, 41–43, 45–48, 50, 55–57 |

## Token use and provider caching (run 1)

| System | Input tokens per question | Cache hits | Uncached input per question | Output tokens per question |
|---|---|---|---|---|
| EduTA | about 21,200 | 32% | about 14,400 | about 1,330 |
| Tool-calling (original configuration) | about 52,200 | 88% | about 6,300 | about 1,390 |
