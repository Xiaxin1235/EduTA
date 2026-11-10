You are an AI reviewer classifying how an already-failed answer misread the indicator hierarchy of a Chinese school-digitization assessment. You do not know which system produced the answer; do not try to identify it. Read only this file and the packets you are given. Do NOT edit any existing file, and never open any `_MASK_MAP_DO_NOT_READ_DURING_REVIEW.json`.

Packets: `<review_dir>\confusion\packets\L###.json`. Each is one answer to a question that should be answered; it failed on task interpretation. Fields include `question`, `target_level` (overall / dimension / sub-dimension / item), `target_indicator`, `mapping`, `reference_facts`, `visible_answer`, `end_to_end_reason`.

The hierarchy: overall score → six dimensions (数字资源, 教育教学, 数字素养, 基础设施, 教育治理, 保障机制) → 13 sub-dimensions (二级维度, e.g. 终端/教室/网络 under 基础设施; 组织保障/财力保障/人力保障 under 保障机制; 学校治理/网络安全 under 教育治理; 资源规模/资源应用 under 数字资源; 教学方式/教学评价 under 教育教学) → 83 items (题项, Q1–Q83).

For each packet decide:

1. `reported_level`: the level of the indicator the answer actually reported — `overall`, `dimension`, `sub-dimension`, `item`, or `none` (no indicator value reported: clarification, refusal, error, empty answer).
2. `relation` of the reported indicator to the target indicator:
   - `same`: the right indicator (the failure lies elsewhere, e.g. wrong object, region, year, filter or statistic);
   - `parent`: a broader level containing the target (e.g. the dimension or overall score instead of a sub-dimension or item);
   - `child`: a narrower level inside the target (e.g. one item or some sub-dimensions instead of the dimension);
   - `sibling`: another indicator at the same level (e.g. another dimension, another item);
   - `unrelated`: a different branch at a different level;
   - `none`: no indicator reported.
3. `object_error`: true if the answer also or instead used the wrong region, school, year, population filter or statistic.

Write ONE file per packet to `<review_dir>\confusion\out\L###.json` (UTF-8, valid JSON):

```json
{"key": "L001", "reported_level": "dimension", "relation": "parent", "object_error": false, "note": "简短中文说明"}
```
