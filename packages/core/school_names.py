"""Number spelling normalization for registered ordinal school names."""
import re

SCHOOL_NAME_SUFFIX = r'初级中学|高级中学|初中|高中|小学|中学|完小|附小|附中|幼儿园|学校|学院|大学'

_ORDINAL=re.compile(r'第([0-9零〇一二两三四五六七八九十百千万]+)(?='+SCHOOL_NAME_SUFFIX+r'|实验|职业)')
_DIGITS='零一二三四五六七八九'

def _integer(text):
    if text.isdigit():return int(text)
    if not any(c in text for c in '十百千万'):
        return int(''.join(str(_DIGITS.index(c.replace('〇','零').replace('两','二'))) for c in text))
    total=section=number=0
    for c in text.replace('两','二'):
        if c in _DIGITS:number=_DIGITS.index(c)
        elif c=='万':total+=(section+number)*10000;section=number=0
        else:section+=(number or 1)*{'十':10,'百':100,'千':1000}[c];number=0
    return total+section+number

def _chinese(number):
    if number<10:return _DIGITS[number]
    for unit,label in [(10000,'万'),(1000,'千'),(100,'百'),(10,'十')]:
        if number>=unit:
            high,low=divmod(number,unit)
            prefix=('' if unit==10 and high==1 else _chinese(high))+label
            return prefix+('零' if low and low<unit//10 else '')+(_chinese(low) if low else '')

def normalize_school_numbers(name,style='arabic'):
    return _ORDINAL.sub(lambda m:'第'+(str(_integer(m[1])) if style=='arabic' else _chinese(_integer(m[1]))),str(name))
