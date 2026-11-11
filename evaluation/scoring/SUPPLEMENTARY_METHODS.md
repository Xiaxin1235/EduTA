# Supplementary methods (details moved from Section 5.1 of the manuscript)

## Data and rule R11

The snapshot contains 374,887 school-year registration records (2020: 115,984; 2021: 93,296; 2022: 75,495; 2023: 90,112) and 31,115,621 answers to 83 items in six dimensions (11 digital resources, 12 education and teaching, 9 digital literacy, 18 infrastructure, 17 education governance, 16 support mechanisms). The unit of analysis is a school-year registration record; the source identifier is not a national school code, so we do not claim that each record is a distinct school. The records cover seven school types (primary schools 57,123; junior secondary 16,623; nine-year 7,409; senior secondary 3,421; teaching points 3,128; complete secondary 1,919; twelve-year 489, in 2023), with no kindergartens or vocational schools.

Under R11, 38, 35, 40 and 28 items count as not collected in 2020–2023 (lists in SCORING_DECISIONS.md); three sub-dimensions are uncollected in some years (assessment of learning in 2020 and 2021, resource use and cyber security in 2023). In 2020 and 2021 the zero items form one contiguous block at the end of the questionnaire (items 46–83 and 49–83), as expected when items are added to a later version; in 2022 they include items whose true value cannot be zero in every school, such as the number of classrooms and the network bandwidth.

Sensitivity (R11 against counting all stored zeros, and against excluding only the 2020/2021 blocks):
- National overall means: 2020 22.27 → 12.08; 2021 21.24 → 12.28; 2022 17.64 → 9.14; 2023 19.72 → 13.07 when zeros are counted. These means are over all records; on the national scope of the reference answers (without the Xinjiang Production and Construction Corps, Hong Kong, Macao and Taiwan; 89,897 records in 2023) the 2023 values are 19.71 and 13.06, as in the main text. The block-only definition agrees with R11 in 2020–2021 and with counting zeros in 2022–2023.
- Within one year and one indicator: ranking of provinces by overall score and by each dimension, of cities within each province, and of all schools identical or nearly so (Spearman 1.0 to three decimals; in 2020 eight of 669 cities changed rank within their province); top-10 schools of every city identical.
- Across years: the direction of change of the overall mean reverses for 19 of 31 provinces and 90 of 198 cities (2020→2021), 0 provinces and 1 of 231 cities (2021→2022), 0 provinces and 16 of 237 cities (2022→2023).
- Across dimensions: the strongest dimension of a province changes for 31, 31, 30 and 15 of 31 provinces in 2020–2023; the weakest for 0, 0, 4 and 0.

## Tool-calling baseline configuration

The model receives the full indicator catalogue, the computation and reporting rules and the seven registered operations as nine functions (the regional ranking is offered separately for regions and for schools, and a multi-year series calls the regional assessment once per year; no function computes anything the seven operations do not). Temperature 0.0; at most eight function calls and nine model turns per question; output budget 16,384 tokens per call; the full prompt is resent with every call; the provider's automatic prompt caching was left as it is; no automatic retry (a transport failure would be recorded as an infrastructure loss and retested once; none occurred). Tool-calling+chart adds EduTA's chart component as a tenth function and is otherwise identical; its regional functions also accept 全国 (the 31 provincial units, school-equal, without the Corps, Hong Kong, Macao and Taiwan), computed with the same national identity as EduTA. One transport failure occurred (run 3, X07); it was retested once and passed.

## Review process

Answers were shuffled within each question under neutral labels (EduTA and No-correction as W/X, the text-to-SQL baselines as P/Q, the rule-based baseline as R, the tool-calling baselines as T); terms on the page that could reveal a configuration were neutralised. EduTA and No-correction had earlier been reviewed by Claude Opus 5.5 without masking; the masked and unmasked reviews agreed on 195 of 200 answers, and the author resolved the five disagreements by the principles of Appendix B.

## External expert check

Sample: 16 questions drawn at random, stratified by group and expected behaviour, and 12 questions whose verdicts had been disputed or decided by the author, each with the answers of EduTA, No-correction, Tool-calling and Direct-SQL (112 answers, shuffled within each question). Agreement with the reported verdicts: 103 of 111 decided answers (92.8%, Cohen's κ = 0.84; 0.80 on the random and 0.87 on the disputed questions); one answer undecided. In six of the eight disagreements the expert was stricter (rejecting the clarification on the ambiguous "Jilin" for EduTA and Direct-SQL; requiring the note that a sub-dimension score is application-defined); in two more lenient. Correct answers on the sample (expert / reported): EduTA 23 / 24, No-correction 20 / 20, Tool-calling 19 / 19, Direct-SQL 10 / 13 of 28.

## Evidence kept per answer

The submitted question, the model requests and responses, the page text, a full-page screenshot, every downloaded chart and the executed SQL (web front end); the requests, responses, tool calls and results (scripted baselines).

## Number traceability

Every number in the model-written text was checked against the numbers computed for the question (three runs): EduTA 2,329 of 2,330 traced (2.5% as sums or differences of computed values); Tool-calling 5,381 of 5,401 (10.6% as sums or differences).

## Answer page (formerly Figure 3)

`figures/fig_screenshot_v6.png`: answer page of EduTA for test question Y02 (two cities compared in 2023, with a bar chart), captured from the test run; the interface is in Chinese. The result table and the chart are generated by the program, the explanation by the model. Two internal experiment labels are masked.

## Views given to the text-to-SQL baselines

Two read-only, non-aggregated temporary views over the frozen research data.

`schools` (one row per school-year registration record): id (record key, not a cross-year school identity), year, province, city, district (normalised), school_name, school_type, school_area_type, class_num, teacher_num, student_num, data_mark, region, raw_province, raw_city, raw_district, query_province, query_city, query_district (equal to the normalised fields), research_status, metadata_source, additional_format_rule.

`answers` (one row per record and item): school_id (joins schools.id), question_id (1–83), value (raw value; 0 is valid, NULL missing), normalized_value (normalised score in [0, 1]; scores are reported ×100), dimension (dimension of the item), question_content (item text).

The documentation also gives the computation and selection rules (school-equal and answer-pooled means, valid-school counts, R11 item lists per year, sub-dimension item sets, school alias rules) and the geography registry.

## Token figures per call and per question (EduTA, run 1)

91 parsing calls (about 7,447 input tokens per call, 98.0% cache hits) and 82 writing calls (about 17,672 per call, 1.7% cache hits) over 100 questions; per question averaged over all 100: input 21,268, uncached input 14,376, output 1,333, total 22,601 (recounted from the model wire log; an earlier count had omitted one parsing call). Tool-calling+chart (three runs): 57,727, 53,234 and 53,171 tokens per question; cache hits 87.7%, 87.8% and 88.7%; uncached input 6,947, 6,332 and 5,901; output 1,087 to 1,190; 2.2 to 2.6 function calls per question.

## Stage attribution of Tool-calling+chart and of EduTA's repeat runs

The failures of Tool-calling+chart in its three runs (13, 17 and 14) were shuffled and masked, and attributed to layers M1–M4 and judged for core correctness by AI reviewers with the rules used for the other systems; the failures of EduTA in runs 2 and 3 (14 and 14) were attributed in the same way in an earlier masked pool. Stages (interpretation / execution / output): Tool-calling+chart 5/1/7, 5/1/11 and 3/2/9 in runs 1–3 (X44 in runs 1 and 3 set to interpretation by the author: a causal question answered with a conclusion); EduTA 14/0/0 in each run. Run 1 of Tool-calling+chart: M1 95/100, M2 81/84, M3 12/12, M4 87/100; core correctness 90/100 (EduTA 86; 6 questions only EduTA, 10 only Tool-calling+chart; exact sign test p = 0.45); two child-level confusions (an item reported for a sub-dimension, X40 and Y32). Questions whose correctness varied across the three runs: 21 only for Tool-calling+chart, 1 only for EduTA (p < 0.001); correct in all three runs 85 (EduTA) and 73 (p = 0.023); EduTA's 13 persistent failures: Tool-calling+chart correct in all three runs on 5 and in some on 7. Open profile questions failed in all three runs: X02 and X30 omitted the national comparison in every run although the national mean was available; X03 gave it in runs 2 and 3 but omitted the provincial rank; X01 used a school-stage subset in every run.


## Boundary of the correction ablation

EduTA (G1NC) and No-correction (G1NPNC) were run from the same sealed source (fingerprint 13a34b27…); the configurations differ only in the run identifier and in one switch, TE_ABLATION_REQUIREMENT_PRESERVATION (1 for EduTA, 0 for No-correction), which gates the question-grounded corrections after the model's draft. The switch also gates one boundary before the parser that closes an explicit unsupported school-item statistic; it did not fire on the test bank. Model wire logs: in both configurations the same nine questions made no model call (X44, X45, X49–X54 declined; X58 clarification), and the same 91 questions were sent to the parser. The causal and significance questions X46 and X47 reached the parser in both; with the corrections the question's own causal or significance wording led to a decline, without them the draft's request for a scope stood. Run-to-run status changes: EduTA 2, No-correction 3 questions; the stability is therefore not attributed to the corrections.


## Question bank examples

Examples from the test bank, translated from Chinese (moved from the appendix of the paper). Identifiers starting with X are realistic questions and those starting with Y standard ones; all 100 questions, with question-level statuses for every system, are released with the code.

- **Answer, an uncollected sub-dimension (Y35)** What is the score of Tai'an's schools for the application of digital resources? (No year, so 2023; every item of the resource-use sub-dimension was uncollected in 2023, so the answer must say so instead of giving a number.)
- **Decline, significance (X46)** Is the difference in school digitization between Jilin and Liaoning in 2023 statistically significant? (Descriptive differences may be given; no significance test can be claimed.)
- **Clarify, ambiguous district (X55)** How is the digitization of schools in Chengguan District? (Chengguan District exists in Lanzhou and in Lhasa.)


## Paired effect sizes (run 1)

EduTA against each system: both correct / only EduTA / only the other / neither; difference in accuracy (EduTA minus other, percentage points) with Newcombe's hybrid-score 95% interval for the difference of paired proportions (Newcombe 1998, method 10: Wilson score limits of the two marginal proportions combined with the within-question correlation phi of the 2x2 table, continuity-corrected when ad > bc); the intervals are not adjusted for multiplicity, only the sign-test p values are Holm-adjusted; exact sign test; Holm-adjusted over the six comparisons (evaluation/scoring/paired_effects.py).

| Other system | Both | Only EduTA | Only other | Neither | Difference (95% CI) | p | p (Holm) |
|---|---|---|---|---|---|---|---|
| No-correction | 72 | 14 | 7 | 7 | 7.0 (-2.2 to 16.3) | 0.19 | 0.38 |
| Rule-based | 51 | 35 | 0 | 14 | 35.0 (25.1 to 44.1) | <0.001 | <0.001 |
| Tool-calling | 63 | 23 | 10 | 4 | 13.0 (1.8 to 23.9) | 0.035 | 0.11 |
| Tool-calling+chart | 77 | 9 | 10 | 4 | -1.0 (-10.0 to 8.0) | 1.00 | 1.00 |
| Direct-SQL | 44 | 42 | 8 | 6 | 34.0 (21.2 to 45.3) | <0.001 | <0.001 |
| MAC-SQL | 34 | 52 | 8 | 6 | 44.0 (30.5 to 55.3) | <0.001 | <0.001 |

## MAC-SQL configuration

Official repository wbbeyourself/MAC-SQL at commit 31a9df5, upstream files unmodified; adapter: external model-API hook, database connection to the two read-only SQLite views, a Refiner subclass for SQL input/output, suppression of the ChatManager connectivity ping. Official prompts, agent routing and MAX_ROUND unchanged; no decline wrapper added; the evidence document is the same R11 documentation given to Direct-SQL; output budget 16,384 tokens for generation and refinement.


## Plausibility of R11

Read-only check on the research data (evaluation/reference_computation/r11_plausibility.py, r11_ambiguous.py). R11 excludes 141 item-years. Pattern: the exclusions form blocks of item numbers, consistent with questionnaire revisions — items 59–83 (25) are present only in 2023; items 49, 51–54, 58 only from 2022; items 50, 55–57 only in 2022; items 46–48 only in 2021; 10 early items (1, 6, 7, 12, 16, 24, 27, 41–43) are absent in 2023, 11 early items absent in 2022 and 2023, item 9 absent in 2022. Probability argument (null hypothesis: the item was asked in that year, and each school independently reports a non-zero value with probability s, the item's non-zero share among schools in the years where it was collected; then P(all n schools report zero) = (1 - s)^n, reported as log10; independence between schools is an assumption, and a non-zero share that differs by year would change the values but not their order of magnitude for items with s above about 0.1%): for each excluded item-year, the share of schools with a non-zero value in the years where the item was collected gives the probability that all schools of that year (75,495 to 115,984 with stored answers) would report zero had the item been asked, assuming independence: below 10^-10 for 134 of the 141 item-years (for example item 81, non-zero share 3.75%, about 10^-1547 to 10^-1923). The remaining seven (2020 item 47; 2022 items 45, 47; 2023 items 41, 42, 45, 47) belong to items that are almost always zero even when collected (non-zero share below 0.01%), so absence and genuine zeros cannot be told apart. Counting only these seven as genuine zeros: national school-equal mean 2020 22.27 → 21.79, 2021 21.24 → 21.24, 2022 17.64 → 16.85, 2023 19.71 → 18.38; provinces whose direction of change differs from R11: 1/31 (2020→2021), 0/31 (2021→2022), 3/31 (2022→2023). Counting all 141 as zeros (the sensitivity analysis above) would require every school to report zero in item-years where this is essentially impossible under the observed non-zero shares. The yearly item lists have not been confirmed by the data provider.
