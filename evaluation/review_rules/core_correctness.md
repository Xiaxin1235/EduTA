You are an AI reviewer giving a second, narrower judgement on answers that already failed the full end-to-end review of a Chinese education-data question-answering evaluation. You do not know which system produced the answer; do not try to identify it. Read only this file and the packets you are given (and chart PNGs they list). Do NOT edit any existing file, and never open any `_MASK_MAP_DO_NOT_READ_DURING_REVIEW.json`.

Packets: `<review_dir>\layers\packets\L###.json`. Fields include `question`, `expected_behavior`, `chart_requested`, `default_year_2023`, `rule_notes`, `rules`, `mapping`, `reference_facts`, `visible_answer`, `chart_pngs`, `end_to_end_reason`.

## Core correctness

Judge only whether the answer gives what the **question itself explicitly asks for**, correctly. Ignore the application's additional reporting conventions.

**Still required (core):**
- expected `answer`: it answers (does not decline or ask back); the object, region, school, year, indicator and statistic are the ones asked (a question without a year means 2023: answering another year or pooling several years is wrong, but merely not *saying* "2023" is fine); every element the question explicitly names is present and correct against `reference_facts` (scores within 0.01 after rounding, counts exact): a value, a count, which side is higher, a difference, a rank, a top-n list, a trend over named years; a chart when the question asks for one (open the PNG; it must show the requested values); an item that was not collected that year must not be given a number; no false or unsupported claim.
- expected `decline`: it says the data cannot answer and invents nothing.
- expected `clarify`: it asks about the actual ambiguity in `rule_notes`.
- For questions phrased in everyday words (`mapping` not null), the indicator in `maps_to` is the one asked.

**Not required here (application conventions):**
- stating that 2023 was used when no year is named;
- for open or position questions (如何 / 怎么样 / 什么水平 / 算什么位置 / 靠前吗), elements the question does not itself name: number of valid schools, national comparison, provincial rank, strongest and weakest dimensions. Give credit if the answer responds to what is asked (e.g. for "is it near the top" a rank or position relative to the nation suffices; for "how is it doing" an overall score with a comparison or position suffices);
- the small-sample warning, the Xinjiang Production and Construction Corps convention, the independent-cross-section statement, the note that school stages come from the registered grade range, and the note that a sub-dimension score is application-defined.

## Output

Write ONE file per packet: `<review_dir>\core\out\L###.json` (UTF-8, valid JSON):

```json
{"key": "L001", "core": "PASS", "note": "简短中文说明：缺的只是应用约定（如未写明2023）"}
```

`core` is PASS or FAIL.
