"""Read-only evidence for a literal school name, before it can change a task."""
from contextlib import closing
import re
import unicodedata


def _name_key(name, city):
    from scripts.school_engine import expand_school_alias
    value=expand_school_alias(re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(name))))
    city=unicodedata.normalize('NFKC', str(city or ''))
    if city.endswith('市') and value.startswith(city[:-1]) and not value.startswith(city):
        value=city+value[len(city)-1:]
    from packages.core.school_names import normalize_school_numbers
    return normalize_school_numbers(value)


def match_school_name(literal, year=None):
    """Exact/normalized unique registered path; fuzzy search only retrieves candidates.

    `schools` is the existing frozen annual research view (including the v5
    metadata overlay), or the caller-selected read-only source outside research.
    Multiple years do not establish historical identity; execution still binds
    the requested annual record through the original school tools.
    """
    from scripts.school_engine import connect, expand_school_alias, find_schools
    literal=str(literal).strip()
    variants=list(dict.fromkeys([literal,expand_school_alias(literal)]))
    with closing(connect()) as conn:
        rows=[dict(r) for r in conn.execute(
            'SELECT id,school_name,province,city,district,year FROM schools WHERE school_name IN ('+
            ','.join('?' for _ in variants)+')'+(' AND year=?' if year else ''),
            (*variants,*([str(year)] if year else [])))]
        if not rows:
            rows=find_schools(literal,year=year,limit=101,connection=conn)
            if len(rows)==101:
                return None  # A truncated candidate set cannot establish uniqueness.
    exact=[r for r in rows if _name_key(r['school_name'],r.get('city'))==_name_key(literal,r.get('city'))]
    paths={(r.get('province'),r.get('city'),r.get('district'),r['school_name']) for r in exact}
    if len(paths)!=1 or year and len({r['id'] for r in exact})!=1:
        return None
    return next(iter(paths))[-1]
