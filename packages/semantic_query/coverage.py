"""Requested measurement explanations from the public questionnaire catalogue."""
import copy, re
from packages.semantic_query.catalogue import load_semantic_catalogue

def attach_catalogue_coverage(public, spec, question):
    public=copy.deepcopy(public)
    # Explanatory text is intentionally not read from the scope mask.
    text=re.sub(r'(?:不|无需|不要)(?:解释|说明)[^，,。；;]*', '', str(question))
    if not re.search(r'(?:说明|解释|包含|覆盖|衡量|测量).{0,45}(?:题目|题项|类型|内容|能力|方面|覆盖范围)|(?:题目|题项).{0,25}(?:类型|包含|覆盖)',text):
        return public
    requested=spec.get('topics') or []
    if requested:
        from packages.semantic_query.catalogue import topic_definition
        items=load_semantic_catalogue()['items'];packet=[];sentences=[]
        for name in requested:
            ids=topic_definition(name)['indicator_ids']
            rows=[dict(question_id=i['id'],name=i['question']) for i in items if i['id'] in ids]
            packet.append(dict(topic=name,items=rows))
            sentences.append('二级维度【'+name+'】包含题项：'+'；'.join('Q'+str(i['question_id'])+' '+i['name'] for i in rows)+'。')
        public['catalogue_coverage']=packet
        public['direct_answer']=' '.join([public.get('direct_answer') or '',*sentences])
        return public
    dims=list(spec.get('target_dimensions') or []) or ([public['target_dimension']] if public.get('target_dimension') else [])
    if not dims or spec.get('granularity') not in {'dimension','dimension_pair'}: return public
    items=load_semantic_catalogue()['items']
    packet=[]; sentences=[]
    for dim in dims:
        rows=[x for x in items if x['dimension']==dim]
        topics={}
        for row in rows:
            topics.setdefault(row.get('topic') or dim,[]).append({'question_id':row['id'], 'name':row.get('summary') or row['question']})
        packet.append({'dimension':dim,'source':'school_digitization_semantics_v1.yaml','topics':[{'name':k,'items':v} for k,v in topics.items()]})
        descriptions=['、'.join(x['name'] for x in v) for v in topics.values()]
        sentences.append(f'【{dim}】目录覆盖：'+ '；'.join(descriptions)+'。')
    public['catalogue_coverage']=packet
    public['direct_answer']=' '.join([public.get('direct_answer') or '',*sentences]).strip()
    return public
