"""可审计的阶段挂接规则。精确标识冲突优先于名称相似度。"""
import re
import unicodedata
from difflib import SequenceMatcher

KEYS=('project_code','regulator_id','contract_id','unit_id','condition_id','approval_id','certificate_id')

def normalize(value):
    if value is None:return ''
    text=unicodedata.normalize('NFKC',str(value)).strip().upper()
    if text in ('','无','暂无','待补','待核实','NULL','NONE','N/A','-','/'):return ''
    return re.sub(r'[\s，,。.;；:：、\[\]【】〔〕()（）_—–\-]','',text)

def area_m2(value,unit):
    if value is None or not str(value).strip():return None
    factor={'平方米':1,'㎡':1,'M2':1,'亩':2000/3,'公顷':10000,'万平方米':10000}.get(str(unit).strip().upper())
    if factor is None:return None
    try:
        result=float(value)*factor
        return result if result>0 else None
    except (TypeError,ValueError):return None

def rank_candidates(target,records):
    from address_match import address_match
    candidates=[]
    for record in records:
        exact=[k for k in KEYS if normalize(target.get(k)) and normalize(target[k])==normalize(record.get(k))]
        conflicts=[k for k in KEYS if normalize(target.get(k)) and normalize(record.get(k)) and normalize(target[k])!=normalize(record[k])]
        name_a,name_b=normalize(target.get('name')),normalize(record.get('name'))
        names=[name_a,*[normalize(n) for n in target.get('name_aliases',[])]]
        name_score=max((SequenceMatcher(None,n,name_b).ratio() for n in names if n and name_b),default=0)
        address_a,address_b=normalize(target.get('address')),normalize(record.get('address'))
        address=address_match(target.get('address'),record.get('address'),target.get('road_aliases'))
        address_score=max([address['score'],*[address_match(v,record.get('address'),target.get('road_aliases'))['score'] for v in target.get('address_aliases',[])]])
        if address['conflicts']:conflicts.append('address_direction')
        a,b=target.get('area_m2'),record.get('area_m2')
        area_error=abs(a-b)/max(a,b) if a and b else None
        area_score=max(0,1-area_error) if area_error is not None else 0
        if not exact and name_score<.45 and address_score<.65 and (area_error is None or area_error>.02):continue
        score=.55*name_score+.25*address_score+.2*area_score
        candidates.append({'record':record,'exact':exact,'conflicts':conflicts,'address_constraints':address,'name_score':round(name_score,4),'address_score':round(address_score,4),'area_error':area_error,'score':round(score,4)})
    return sorted(candidates,key=lambda c:(bool(c['exact']) and not c['conflicts'],len(c['exact']),c['score']),reverse=True)

def decide(target,records):
    candidates=rank_candidates(target,records)
    exact=[c for c in candidates if c['exact'] and not c['conflicts']]
    # 同一项目代码可以覆盖多宗地，不能默认它是一对一主键。
    if len(exact)==1:return ('MATCH_EXACT' if any(k not in target.get('inferred_keys',[]) for k in exact[0]['exact']) else 'NEEDS_FUZZY'),exact
    if len(exact)>1:
        parcel_keys=('regulator_id','contract_id','unit_id')
        specific=[c for c in exact if any(k in c['exact'] for k in parcel_keys)]
        if len(specific)==1:return ('MATCH_EXACT' if any(k not in target.get('inferred_keys',[]) for k in specific[0]['exact']) else 'NEEDS_FUZZY'),specific
        return 'AMBIGUOUS',exact
    if any(c['exact'] and c['conflicts'] for c in candidates):return 'CONFLICT',candidates[:5]
    if not candidates:return 'NOT_FOUND',[]
    if len(candidates)>1 and abs(candidates[0]['score']-candidates[1]['score'])<.08:return 'AMBIGUOUS',candidates[:5]
    # shortcut: 模糊相似度仅供候选排序，积累人工标注样本后再校准自动通过阈值。
    return 'NEEDS_FUZZY',candidates[:5]
