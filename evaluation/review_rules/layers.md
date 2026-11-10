You are an AI reviewer attributing already-failed answers of a Chinese education-data question-answering evaluation to layers. Each packet is one answer that was judged FAIL end-to-end (the reason is given in `end_to_end_reason`). You do not know which system produced it; do not try to identify it. Read only this file and the packet files you are given (and chart PNGs they list). Do NOT edit any existing file, and never open `_MASK_MAP_DO_NOT_READ_DURING_REVIEW.json`.

Packets: `<review_dir>\layers\packets\L###.json` (UTF-8). Fields: `question`, `expected_behavior` (answer / decline / clarify), `chart_requested`, `default_year_2023`, `rule_notes`, `rules` (including the sub-dimension rules S1–S3), `mapping` (null, or the frozen mapping of everyday wording to an indicator), `reference_facts`, `visible_answer` (what the user saw; tables count as stated), `chart_pngs`, `end_to_end_reason`, `missing_or_wrong`.

## The four layers: give each one OK / WRONG / NA

**M1 task mapping**: did the answer address the right task?
- answer questions: it answered (did not decline or ask back) and used the right object, region, year (rule D: no year in the question means 2023), metric (overall, dimension, sub-dimension or item; for a non-null `mapping` the indicator in `maps_to`), and statistic. Using a different subset (e.g. only 中小学 when the reference is all schools), a different metric (a single item instead of a dimension score), the wrong year or region, or a normalized score where a raw count is asked = WRONG.
- decline questions: it clearly declined (data cannot answer / beyond capability) = OK; it answered as if the data existed, or produced a value for a non-existent object = WRONG.
- clarify questions: it asked a question that targets the actual ambiguity = OK; it guessed and answered, or asked something generic = WRONG.

**M2 numbers and calculation**: are the core requested values present and correct against `reference_facts` (tolerances: scores 0.01 after rounding to 2 decimals or computed from displayed rounded values; counts exact; percentages 0.01)?
- answer questions: the core value(s) the question asks for are missing or wrong = WRONG. If M1 is WRONG, still judge the numbers against the reference (they will usually be WRONG).
- decline / clarify questions: NA, unless the answer gives descriptive numbers that `reference_facts` can check; then judge those.

**M3 chart**: NA when `chart_requested` is false. When true: a chart that shows the requested objects and values correctly = OK; missing or wrong = WRONG.

**M4 text coverage**: everything else that the bank rules require in the text, and no false claims:
- other explicitly requested elements beyond the core value (school count, comparison side, rank, strongest/weakest dimension, etc.);
- R-open elements for 如何/怎么样/好不好/处于什么水平/算什么位置 questions;
- the note that a sub-dimension score is an application-defined item mean is NOT required (do not mark M4 WRONG for its absence);
- rule statements: D (says 2023), R3 small-sample warning, R4 新疆 / 兵团, R5 independent cross-sections, R9 stage from registered grade range, R11 not collected that year;
- for decline: states that the data does not contain it / cannot answer, and fabricates nothing; for clarify: the question wording itself;
- no false or unsupported claim (wrong statement of fact, causal claim, prediction, statement that contradicts its own result).

Attribute each failure to every layer that actually failed; one answer can fail several layers. Use the `end_to_end_reason` as a starting point, but check it against the answer and reference. At least one layer must be WRONG (the answer failed end-to-end); if you believe none failed, still mark the layer the end-to-end reason names and say so in the note.

## Output

Write ONE file per packet: `<review_dir>\layers\out\L###.json` (UTF-8, valid JSON):

```json
{"key": "L001", "M1": "OK", "M2": "WRONG", "M3": "NA", "M4": "WRONG", "note": "简短中文说明，引用关键数字或句子"}
```


Uniform conventions (apply them yourself): an answerable question that received no answer (declined, asked back, error) is WRONG on M1, M2 and M4; M3 is WRONG whenever a requested chart image is absent or does not show the requested values.
