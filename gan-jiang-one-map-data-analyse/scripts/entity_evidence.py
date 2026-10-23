"""保留段落/表格实体边界和OCR修正依据，不跨项目拼接字段。"""
import re
import unicodedata
from match_engine import normalize
from material_evidence import IDENTIFIERS,material_stage

HEADER=re.compile(r'(?:建设项目名称|项目名称)\s*[:：]')
TITLES={'预审与选址':'建设项目选址意见书','规划条件与红线':'建设用地规划条件','用地报批':'建设用地批复','土地供应':'国有建设用地使用权出让合同'}

def entity_blocks(text,layout=None):
    lines=text.splitlines()
    for index,line in enumerate(lines):
        headers=[v.strip() for v in line.split('\t')]
        if '项目名称' not in headers or len(headers)<2:continue
        rows=[]
        for row in lines[index+1:]:
            values=[v.strip() for v in row.split('\t')]
            if len(values)!=len(headers):continue
            pairs=dict(zip(headers,values));unit=pairs.get('面积单位') or pairs.get('用地面积单位','')
            fields=['{}：{}{}'.format(k,v,unit if k in ('拟用地面积','用地面积','宗地面积') and not re.search('亩|平方米|公顷',v) else '') for k,v in pairs.items() if v]
            rows.append(dict(id='row-'+str(len(rows)+1),text=TITLES.get(material_stage(text),'')+'；'+'；'.join(fields)+'；',method='表格行分段'))
        if rows:return rows
    # OCR有坐标时先分栏，避免左右两列同一行交错。
    if layout:
        words=layout.get('words',[])
        anchors=[w for w in words if '项目名称' in w['text']]
        xs=sorted({round(w['x']/40)*40 for w in anchors})
        if len(xs)==2 and xs[1]-xs[0]>100:
            middle=(xs[0]+xs[1])/2;columns=[]
            for lo,hi in [(-float('inf'),middle),(middle,float('inf'))]:
                selected=sorted([w for w in words if lo<=w['x']<hi],key=lambda w:(round(w['y']/10),w['x']))
                if selected:
                    heading=TITLES.get(material_stage(text),'')
                    blocks=entity_blocks(heading+'\n'+'\n'.join(w['text'] for w in selected))
                    columns.extend(dict(b,method='OCR坐标分栏；'+b['method']) for b in blocks)
            if len(columns)>1:return columns
    starts=list(HEADER.finditer(text))
    if len(starts)<2:return [dict(id='block-1',text=text,method='单实体或无法可靠分段')]
    prefix=text[:starts[0].start()]
    return [dict(id='block-'+str(i+1),text=(prefix if i==0 else TITLES.get(material_stage(prefix),'')+'\n')+text[m.start():starts[i+1].start() if i+1<len(starts) else len(text)],method='重复项目名称标签分段') for i,m in enumerate(starts)]

def ocr_identifier_candidates(text,targets,fields):
    compact=unicodedata.normalize('NFKC',text).upper()
    tokens=set(re.findall(r'(?<![A-Z0-9])[A-Z0-9\-一—]{10,40}(?![A-Z0-9])',compact))
    lookalike=str.maketrans({'O':'0','I':'1','L':'1','S':'5','B':'8','Z':'2','G':'6','一':'-','—':'-'})
    corrections=[]
    for token in tokens:
        options=[]
        for target in targets:
            for key in IDENTIFIERS:
                value=str(target.get(key) or '').upper()
                if not value or len(token)!=len(value) or token==value:continue
                differences=sum(a!=b for a,b in zip(token,value))
                if differences==1 and token.translate(lookalike)==value.translate(lookalike):options.append((key,value))
        unique=set(options)
        if len(unique)!=1:continue
        key,value=unique.pop()
        # 已识别出合法但不同的编号时不替换，保留冲突交给复核。
        if fields.get(key):continue
        fields[key]=[value];corrections.append(dict(key=key,original=token,candidate=value,reason='单字符形近OCR候选，需核对原件'))
    return corrections
