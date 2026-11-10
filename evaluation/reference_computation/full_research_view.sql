CREATE TEMP VIEW research_schools AS
SELECT b.*, CASE WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_province ELSE b.normalized_province END AS query_province,
CASE WHEN f.source_school_year_id IS NOT NULL THEN f.new_value WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_city ELSE b.normalized_city END AS query_city,
CASE WHEN m.source_school_year_id IS NOT NULL THEN m.normalized_district ELSE b.normalized_district END AS query_district,
r.status AS research_status
FROM base.school_records b
LEFT JOIN mapping.record_metadata m ON m.source_school_year_id=b.id AND m.year=b.year
LEFT JOIN mapping.format_extension_v5 f ON f.source_school_year_id=b.id AND f.year=b.year
LEFT JOIN mapping.research_review_v5 r ON r.source_school_year_id=b.id AND r.year=b.year;
