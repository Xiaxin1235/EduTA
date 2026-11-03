"""Versioned, deterministic, question-relevant annotations; no model calls."""
import json
from functools import lru_cache
from pathlib import Path

@lru_cache(maxsize=1)
def rules():
    return json.loads((Path(__file__).resolve().parents[2]/'research_rules/CATALOGUE_ANNOTATIONS_v2.json').read_text('utf8'))

def select(question,catalogue):
    data=rules();text=str(question).lower()
    available={x['id'] for x in catalogue}
    dimensions=[d for d in data['dimensions'] if any(w.lower() in text for w in d['cues'])]
    from packages.semantic_query.catalogue import extract_explicit_question_ids
    explicit=set(extract_explicit_question_ids(question))
    grams={text[i:i+2] for i in range(len(text)-1)}
    scored=[]
    for item in data['items']:
        if item['id'] not in available:continue
        hits=[w for w in item['cues'] if len(w)>=3 and w.lower() in text]
        overlap=len(grams & {item['meaning'][i:i+2] for i in range(len(item['meaning'])-1)})
        score=1000*(item['id'] in explicit)+20*bool(hits)+overlap
        if item['id'] in explicit or hits or overlap>=3:
            scored.append((score,item))
    ordered=[i for _,i in sorted(scored,key=lambda p:(-p[0],p[1]['id']))]
    selected=[i for i in ordered if i['id'] in explicit]+[i for i in ordered if i['id'] not in explicit][:8]
    names={d['dimension'] for d in dimensions}|{i['dimension'] for i in selected}
    if not names and any(w in text for w in ('怎么样','如何','什么水平','概况')):
        names={d['dimension'] for d in data['dimensions']}
    dims=[dict(d) for d in data['dimensions'] if d['dimension'] in names]
    items=[{k:v for k,v in i.items() if k!='cues'} for i in selected]
    ids={i['id'] for i in selected}
    pairs=[p for p in data['distinctions'] if ids & set(p['items'])]
    return dict(version=data['version'],dimensions=dims,items=items,distinctions=pairs,
                selected_dimension_ids=sorted(names),selected_item_ids=sorted(ids))
