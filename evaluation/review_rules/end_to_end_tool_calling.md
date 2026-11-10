You are an evaluator for a Chinese education-data question-answering system. Each packet contains one question and one anonymous system answer (label T) to be judged against a fixed reference. You are an AI reviewer: you see the reference, but not which system configuration produced the answer. Do NOT try to identify the system. Read only this rules file, the packet files you are given, and the chart PNGs those packets list. Do NOT edit any existing file.

Packets: `<review_dir>\<RUN>\packets\<QID>.json` (UTF-8).

## Packet content

Each packet has:
- `question`: the user's question, verbatim;
- `expected_behavior`:
  - `answer`: the system should answer;
  - `decline`: the system should state that the data cannot answer it, or that the request is beyond its capability;
  - `clarify`: the system should ask a targeted question;
- `default_year_2023`: true when the question names no year (rule D below);
- `rule_notes`, and `rules` (the bank rules: open-question elements, missing-year default, R1–R11, and the sub-dimension rules S1–S3);
- `mapping`: null, or for a question phrased in everyday words, the frozen mapping `{phrase, maps_to, type}` (type A/B, see below);
- `reference_facts`: each with `desc` and `value`, computed independently from the frozen data;
- `arms` → `T` → `visible_answer` (the full answer text the user saw: data cards, tables and prose all count), `chart_pngs`, `business_status`.

## Decision per arm: one status, PASS / FAIL / REVIEW (or CLARIFY_OK, mapping type B only), with a short Chinese reason

### expected_behavior = answer

PASS only if all of the following hold.

1. **Every element the question explicitly asks for is present and correct.** Examples: a score, a count, which side is higher, a difference, an order, a rank, a top-3 list, a chart.
2. **Every rule-mandated element is present:**
   - **R-open** (the question asks how a region is doing: 如何 / 怎么样 / 好不好 / 处于什么水平 / 算什么位置). The answer must give:
     - the overall score and the number of valid schools;
     - its position against the national average (higher or lower; a difference is fine);
     - for a province, its rank among the 31 provincial units;
     - the strongest and weakest of the six dimensions.
   - **Rule D** (`default_year_2023` is true): the answer must use 2023 and say so, e.g. “以下为 2023 年数据”. Answering with another year, or asking which year, is a FAIL.
   - **R3** (a reference fact flags fewer than 30 valid schools): the answer must warn that the sample is too small.
   - **R4** (新疆): the answer must use 新疆维吾尔自治区 and state that 新疆生产建设兵团 is not included or is listed separately.
   - **R5** (several years): the answer must say that each year is an independent cross-section and the schools are not the same. Claiming that the same schools improved is a FAIL.
   - **R9** (school stage in 2020–2022): the answer must say that the stage was determined from the registered grade range.
   - **R11** (items not collected in a year count as missing): when the question asks for an item's value in a year where that item was not collected (a reference fact says so), the answer must say it was not collected that year; answering 0 or another number is a FAIL. Scores in the reference already exclude uncollected items, so judge scores only against the reference values.
   - **S1–S3 (sub-dimensions).** A sub-dimension score must match the reference value for that sub-dimension. S1 also asks the answer to note that the score is an item mean not separately defined by the evaluation scheme: a missing note alone is NOT a failure (same treatment as for the other systems scored on this bank). S2: when the question names one sub-dimension, it wants that sub-dimension; when it lists some sub-dimensions of one dimension, a score for each; when it lists all of them, the dimension; a concrete count wants the raw item value. S3: a sub-dimension whose items were all uncollected in a year must be stated as not collected, not 0.
   - **Mapping (`mapping` not null).** The question uses everyday words that map to the metric in `maps_to`. Type A: the answer must report `maps_to`; asking a clarification instead, or reporting a different metric, is a FAIL. Type B: reporting `maps_to` correctly is PASS; a clarification that explicitly lists `maps_to` among its candidates gets status `CLARIFY_OK`; reporting a different metric is a FAIL.
3. **Numbers match the reference.**
   - Scores: within 0.01 after rounding to 2 decimals. A difference computed from the two displayed rounded values is also correct.
   - Counts and raw item values: exact.
   - Percentages: within 0.01.
   - A number attached to the wrong object, year or metric is wrong.
4. **No false or unsupported claim.** Examples of failures: a wrong statement of fact, a causal explanation, a prediction, or describing a guarantee score as money. Correct extra information is allowed.
5. **Position questions.** A question that asks only where a region stands (e.g. 在全国排第几、是否靠前, without asking how it is doing overall) needs the rank or the gap to the benchmark; the dimension breakdown of R-open is not required.
6. **The system did not ask a clarification question instead of answering.** All `answer` questions are answerable; asking back is a FAIL.

If a chart is requested, open the PNG. It must show the requested objects and values correctly; a missing or wrong chart is a FAIL.

### expected_behavior = decline

PASS if the answer clearly states that the data does not contain the requested information, or that it cannot answer this kind of question (cause, prediction, significance, teacher-level data, a year or region outside the data, a school not found), **and** it fabricates nothing.

- Descriptive numbers are allowed if they are correct per `reference_facts` (when given). A wrong descriptive number is a FAIL.
- Offering nearby available information (e.g. the 2023 results when 2024 is asked) is fine.
- Inventing a cause, a forecast, a significance result, a money amount, teacher names, or a score for a non-existent school or region is a FAIL.
- Asking the user a question is acceptable only if it does not pretend data exists.

### expected_behavior = clarify

PASS if the system asks a question that targets the actual ambiguity in `rule_notes`. Examples:
- 吉林省 or 吉林市;
- which city's 朝阳区;
- which province or object “这个省” or “它” refers to.

FAIL if it guesses one reading and answers, or asks something generic that does not address the ambiguity.

### General

- `business_status` ERROR with no answer text: FAIL. Infrastructure cases were already removed before review.
- `business_status` null with `http_status` 400 means the system rejected its own request and showed no answer: FAIL.
- An arm with `NE_infrastructure: true` is not reviewed: write status `NE` with reason "基础设施故障".
- Use REVIEW only when you genuinely cannot decide from the evidence, for example an unreadable chart; say why.
- Be strict and evidence-based: cite the number or sentence you relied on.

## Output

Write ONE file per question: `<review_dir>\<RUN>\decisions\<QID>.json` (UTF-8, valid JSON):

```json
{"question_id": "<QID>", "reviewer": "claude-sonnet AI reviewer, arm-masked, non-blind to reference",
 "arms": {"T": {"status": "FAIL", "reason": "...", "missing_or_wrong": ["..."]}}}
```

