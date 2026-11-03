"""Affirmative inference requests; school labels and negated requests are not asks."""
import re

def request_text(question):
    from packages.semantic_query import runtime as rt
    from packages.core.geography_registry import mentions
    text=rt._scope_assertion_text(str(question))
    for name in rt._lexical_school_name_spans(text):text=text.replace(name,' '*len(name))
    spans=mentions(text)
    for span in reversed(spans):text=text[:span['start']]+' '*(span['end']-span['start'])+text[span['end']:]
    return text


def inference_text(question):
    text=request_text(question)
    # Contrast and clause boundaries terminate a negation's scope.
    clauses=re.split(r'([，,。；;！？!?]|但是|不过|但)',text)
    for i,clause in enumerate(clauses):
        if re.search(r'(?:不|别|无需|不用|不要|不必)(?:再|去)?(?:推断|推测|分析|解释|讨论|预测|预估|判断|做|作|进行|检验|考察|考虑)',clause):
            clauses[i]=' '*len(clause)
    return ''.join(clauses)

def open_profile(question,*,overview=False):
    return bool(re.search(r'怎么样|如何|好不好|什么水平|什么位置|算.{0,4}位置|排得上号'+(r'|概况' if overview else ''),question))


def money_amount_request(question):
    text=request_text(question)
    if re.search(r'金额|多少钱|多少(?:万|亿)?元|几(?:万|亿)?元|\d+\s*(?:万|亿)?元',text):return True
    from packages.semantic_query.catalogue import topic_scope
    secondary=topic_scope(text)
    if secondary and secondary['topics'] and re.search(r'得分|均分|分数|多少分|水平|怎么样|如何',text):
        return False
    return not re.search(r'占比|比例|百分比|百分之|%',text) and bool(re.search(r'(?:经费|资金|投入|拨款|预算).{0,8}(?:多少|多大|几万)',text))

def score_comparison(question):
    return bool(re.search(r'谁|哪个|哪边|哪一',question) and re.search(r'做得好|更强|更好|数字化水平.{0,5}高|平均分.{0,5}高',question))


def score_requested(question):
    from packages.semantic_query import runtime as rt
    text=rt._scope_assertion_text(question)
    return bool(rt._REGION_SCORE_REQUEST_RE.search(text) or re.search(r'数字化水平|(?:整体|综合)得分|谁(?:更)?强|数字化谁强',text))


def school_count_requested(question):
    """Count nouns and counting questions, rather than the 数字化 prefix."""
    from packages.semantic_query import runtime as rt
    text=rt._scope_assertion_text(question)
    return bool(re.search(r'有效学校数|学校数量|学校数目|学校总数|学校分母|样本(?:学校)?数|'
        r'学校数(?=$|[，,。？?；;、\s]|是多少|为|分别|相差|差|和|与|及)|'
        r'(?:多少|几)所(?:有效|样本|参与|纳入)?学校|'
        r'学校(?:共有|有)?(?:多少|几)所|(?:算进去|纳入|参与计算).{0,10}学校.{0,8}(?:多少|几)所',text))


def province_position(question,candidate=None):
    candidate=candidate or {}
    return bool(re.search(r'省内|所在省|本省|省里|省(?:的|内)?各市|省的.{0,6}(?:地级市|城市)|全省.{0,6}(?:名次|位置|排名)',question) or
        candidate.get('intent')=='region_position' and candidate.get('benchmark')=='province')


def bound_item_ids(question,candidate,literal_ids,catalogue):
    """A partial literal list is not a contradiction of a registered model list."""
    from packages.semantic_query.catalogue import _question_number,extract_explicit_question_ids
    explicit=extract_explicit_question_ids(question)
    if explicit:return explicit
    ids=list(dict.fromkeys(candidate.get('question_ids') or []))
    if not ids or any(type(i)!=int or i not in catalogue for i in ids):return list(literal_ids)
    count=re.search(r'([两二三四五六七八九十\d]+)(?:个|道)?题项|([两二三四五六七八九十\d]+)(?:个|道)题',question)
    size=_question_number(next(g for g in count.groups() if g)) if count else None
    if size==len(ids) and set(literal_ids)<=set(ids):return ids
    if (len(ids)==1 and candidate.get('value_view')=='raw_value' and
        catalogue[ids[0]].get('value_type')=='count' and re.search(r'原始|种数|几种|多少种',question) and
        not re.search(r'维度(?:得分|均分|分数)|六维|整体(?:得分|均分)',question) and
        (not literal_ids or set(literal_ids)==set(ids))):return ids
    return list(literal_ids)
