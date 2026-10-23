"""四至是方位约束；字符相似不能抵消相反方位。"""
import re
import unicodedata
from difflib import SequenceMatcher
from match_engine import normalize

def boundaries(value,aliases=None):
    text=re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value or '')).upper());aliases=aliases or {}
    for alias,canonical in sorted(aliases.items(),key=lambda p:len(p[0]),reverse=True):text=text.replace(normalize(alias),normalize(canonical))
    parts=re.split(r'[、,，;；。]|位于|坐落于|新区|开发区|组团|范围内|[:：]',text)
    result={}
    road=r'([\u4e00-\u9fffA-Z0-9]{1,30}?(?:大道|大街|公路|铁路|路|街|河|湖))'
    opposite={'东':'西','西':'东','南':'北','北':'南'}
    for part in parts:
        for m in re.finditer(road+r'以([东南西北])',part):result.setdefault(m[1],set()).add(m[2])
        for m in re.finditer(r'([东南西北])(?:至|临|接|靠|邻)'+road,part):result.setdefault(m[2],set()).add(opposite[m[1]])
    return result

def address_match(a,b,aliases=None):
    left,right=boundaries(a,aliases),boundaries(b,aliases)
    shared=left.keys()&right.keys();opposite={'东':'西','西':'东','南':'北','北':'南'}
    conflicts=[road for road in shared if any(opposite[d] in right[road] for d in left[road])]
    agreement=sum(bool(left[r]&right[r]) for r in shared)
    x,y=normalize(a),normalize(b)
    score=SequenceMatcher(None,x,y).ratio() if x and y else 0
    if left and right:score=max(score,agreement/max(len(left),len(right)))
    return dict(score=0 if conflicts else round(score,4),conflicts=sorted(conflicts),
                left={r:sorted(d) for r,d in left.items()},right={r:sorted(d) for r,d in right.items()})
