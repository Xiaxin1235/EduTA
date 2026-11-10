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

The model receives the full indicator catalogue, the computation and reporting rules and the seven registered operations as nine functions (the regional ranking is offered separately for regions and for schools, and a multi-year series calls the regional assessment once per year; no function computes anything the seven operations do not). Temperature 0.0; at most eight function calls and nine model turns per question; output budget 16,384 tokens per call; the full prompt is resent with every call; the provider's automatic prompt caching was left as it is; no automatic retry (a transport failure would be recorded as an infrastructure loss and retested once; none occurred). Tool-calling+chart adds EduTA's chart component as a tenth function and is otherwise identical.

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

90 parsing calls (about 7,447 input tokens per call, 98.0% cache hits) and 82 writing calls (about 17,672 per call, 1.7% cache hits) over 100 questions; per question averaged over all 100: input 21,193, uncached input 14,375, output 1,331, total 22,524. Tool-calling+chart (three runs): 53,322, 54,020 and 57,810 tokens per question; cache hits 90.1%, 89.5% and 88.7%; uncached input 5,162, 5,574 and 6,421; output 1,152 to 1,218.

## Stage attribution of Tool-calling+chart and of EduTA's repeat runs

The failures of Tool-calling+chart in its three runs (12, 10 and 11) and of EduTA in runs 2 and 3 (14 and 14) were pooled, shuffled and masked (61 answers), and attributed to layers M1–M4 and judged for core correctness by AI reviewers with the rules used for the other systems. Stages (interpretation / execution / output): Tool-calling+chart 2/1/9, 4/1/5 and 3/2/6 in runs 1–3 (run 1, X44, set to interpretation by the author for consistency with runs 2 and 3); EduTA 14/0/0 in each of runs 2 and 3 (run 1: 14/0/0). Run 1 of Tool-calling+chart: M1 98/100, M2 82/84, M3 12/12, M4 88/100; core correctness 93/100 (EduTA 86; 4 questions only EduTA, 11 only Tool-calling+chart; exact sign test p = 0.12); no level confusion. Questions whose correctness varied across the three runs: 10 only for Tool-calling+chart, 1 only for EduTA (p = 0.012); correct in all three runs 85 (EduTA) and 82 (p = 0.66).
