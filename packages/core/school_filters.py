"""One fixed SQL definition for school cohorts in execution and replay."""
import re

# Generic cohort language has no stage restriction, rather than a new SQL category.
ALL_SCHOOL_COHORT = re.compile(r'中小学校|中小学|各类学校|全部学校|所有学校|全体学校|全部学段')


def generic_school_text(text):
    """Keep a school population noun while suppressing false 小学 substrings."""
    return ALL_SCHOOL_COHORT.sub(lambda m: '学校'+' '*max(0,len(m.group())-2), text)


def normalize_school_filter(value):
    """Return (canonical filter, recognized); SQL cohort definitions stay fixed."""
    from packages.analysis_contract.capabilities import filter_is_structured
    if value is None:
        return None, True
    if not isinstance(value,str):
        return value, False
    value=ALL_SCHOOL_COHORT.sub('',value).strip()
    return (None,True) if not value else (value,filter_is_structured(value))


STAGE_NOTE = '该年学段按登记的年级范围判定。'


def filter_notes(year, school_filter):
    return [STAGE_NOTE] if str(year) in {'2020', '2021', '2022'} and re.search(
        r'小学|初中|高中|初级中学|高级中学|幼儿园|中职|一贯制', school_filter or '') else []


def filter_sql(school_filter):
    from packages.analysis_contract.capabilities import CapabilityError, filter_is_structured
    if not school_filter:
        return '', ()
    clauses = []
    if '九年一贯制' in school_filter:
        clauses.append("(s.school_type LIKE '%九年一贯%' OR s.school_type='1-9' OR s.school_type LIKE 'D.%')")
    elif '十二年一贯制' in school_filter:
        clauses.append("(s.school_type LIKE '%十二年一贯%' OR s.school_type='1-12')")
    elif '中职' in school_filter:
        clauses.append("(s.school_type LIKE '%中职%' OR s.school_type LIKE '%中等职业%' OR s.school_type LIKE '%职业学校%')")
    elif '幼儿园' in school_filter:
        clauses.append("s.school_type LIKE '%幼儿园%'")
    elif '初中' in school_filter or '初级中学' in school_filter:
        clauses.append("(s.school_type LIKE '%初中%' OR s.school_type LIKE '%初级中学%' OR s.school_type LIKE '7-9%' OR s.school_type='1-9' OR s.school_type LIKE 'B.%')")
    elif '小学' in school_filter:
        clauses.append("(s.school_type LIKE '%小学%' OR s.school_type LIKE '1-6%' OR s.school_type LIKE '1-5%' OR s.school_type LIKE 'A.%')")
    elif '高中' in school_filter or '高级中学' in school_filter:
        clauses.append("(s.school_type LIKE '%高中%' OR s.school_type LIKE '%高级中学%' OR s.school_type LIKE '10-12%' OR s.school_type LIKE 'C.%')")
    if '乡村' in school_filter or '农村' in school_filter:
        clauses.append("s.school_area_type='乡村'")
    elif '城镇' in school_filter:
        clauses.append("s.school_area_type IN ('城区','镇区')")
    elif '城区' in school_filter or '城市' in school_filter:
        clauses.append("s.school_area_type='城区'")
    elif '镇区' in school_filter:
        clauses.append("s.school_area_type='镇区'")
    if not clauses or not filter_is_structured(school_filter):
        raise CapabilityError('unsupported_filter:'+school_filter)
    return ' AND ' + ' AND '.join(clauses), ()


def area_assertion_text(text):
    """城市 is an area cohort only when explicitly paired with rural schools."""
    import re
    if not (re.search(r'城市(?:学校|校)', text) and re.search(r'(?:农村|乡村)(?:学校|校)', text)):
        text = text.replace('城市', '城域')
    return text
