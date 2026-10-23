"""供应信息优先的页内归属与跨阶段字段候选；目录只提供检索线索。"""
import datetime as dt
import math
import re
import unicodedata
from match_engine import normalize

CERTIFICATE_NUMBER=r'[A-Z0-9]{12,28}号?(?![A-Z0-9])'
PATTERNS={
    'project_code':r'(?<!\d)\d{4}[-一—－]\d{6}[-一—－]\d{2}[-一—－]\d{2}[-一—－]\d{6}(?!\d)',
    'regulator_id':r'(?<![A-Z0-9])\d{6}20\d{2}[AB]\d{4,6}(?![A-Z0-9])',
    'contract_id':r'(?<![A-Z0-9])H\d{14,18}(?![A-Z0-9])',
    'unit_id':r'(?<![A-Z0-9])\d{12}[A-Z]{2}\d{5}[A-Z]\d{8}(?![A-Z0-9])',
    'certificate_id':r'(?:建字第|地字第|用字第)'+CERTIFICATE_NUMBER,
    'approval_id':r'[赣洪科经][\u4e00-\u9fff]{1,12}[〔【\[(（]\d{4}[〕】\])）]\d{1,5}号',
    'condition_id':r'(?:出具编号|规划条件编号|条件编号)\s*[:：]\s*([^，,。；;:：\n]{3,50}?)(?=项目名称|项目代码|用地面积|用地性质|用地位置|出具日期|出具依据|[，,。；;\n]|$)',
    'date':r'20\d{2}年\d{1,2}月\d{1,2}日',
    'area_text':r'(?:项目用地约|拟用地面积约|拟用地面积|用地面积约|宗地面积)[^。;；]{0,40}?\d+(?:[.．·]\d+)?(?:万平方米|平方米|公顷|亩|㎡|M2)',
}
IDENTIFIERS=('project_code','regulator_id','contract_id','unit_id','condition_id','approval_id','certificate_id')


def compact_text(text):
    return unicodedata.normalize('NFKC',re.sub(r'\s+','',text)).upper()


def canonical_code(value):
    return re.sub(r'[一—－]','-',value)


def page_evidence(text,targets):
    compact=compact_text(text)
    original=unicodedata.normalize('NFKC',text).upper()
    fields={k:sorted(set(re.findall(p,compact))|set(re.findall(p,original))) for k,p in PATTERNS.items()}
    fields['certificate_id']=sorted(set(re.findall(PATTERNS['certificate_id'],compact)))
    fields['project_code']=sorted({canonical_code(v) for v in fields['project_code']})
    normalized=normalize(compact)
    names=[t['project'] for t in targets if len(normalize(t.get('name')))>=4 and normalize(t['name']) in normalized]
    for target in targets:
        for alias in target.get('name_aliases',[]):
            # 供应简称只在项目名称标签后用于归属，不能用地址中出现的道路名确认项目。
            if re.search(r'(?:项目名称|建设项目名称)[:：]?'+re.escape(compact_text(alias))+r'(?:道路工程|工程|项目)?(?=建设单位|单位名称|项目代码|项目拟选位置|拟用地|[，,。；;]|$)',compact):
                names.append(target['project'])
    matches=[];conflicts=[];strong=[]
    for t in targets:
        exact=[k for k in IDENTIFIERS if normalize(t.get(k)) and any(normalize(v)==normalize(t[k]) for v in fields[k])]
        conflict=[k for k in IDENTIFIERS if normalize(t.get(k)) and fields[k] and any(normalize(v)!=normalize(t[k]) for v in fields[k])]
        specific=any(k in ('regulator_id','contract_id','unit_id') and k not in t.get('inferred_keys',[]) for k in exact)
        if t['project'] in names or specific:
            if conflict:conflicts.append(t['project'])
            else:strong.append(t['project'])
        if t['project'] in names or exact:matches.append(t['project'])
    # 项目代码可能覆盖多个宗地，即使在本次目标表里只命中一行，也不能单独确认页内归属。
    projects=sorted(set(strong)) if len(set(strong)|set(conflicts)|set(names))==1 and not conflicts else []
    return dict(fields={k:v for k,v in fields.items() if v},page_project_candidates=sorted(set(names)|set(matches)|set(conflicts)),
                projects=projects,identity_conflicts=conflicts,
                identity_reason='页内名称或已有供应标识唯一命中且无冲突' if projects else '页内归属不唯一、存在标识冲突或无可靠标识')


def material_stage(text):
    head=compact_text(text)[:300]
    # shortcut: 只按正文前部标题分类，未识别标题的材料留待复核，不从目录猜字段阶段。
    titles=[('预审与选址',r'建设项目(?:用地预审与)?选址意见书|用地预审意见'),
            ('规划条件与红线',r'(?:建设用地|用地规划|规划设计|用地规划设计)条件'),
            ('土地供应',r'国有建设用地使用权出让合同|国有土地使用权出让合同|(?:国有建设用地|国有土地|用地)(?:使用权)?划拨决定书'),
            ('用地报批',r'关于[^。；;]{0,100}建设用地[^。；;]{0,30}批复|建设用地批复')]
    matches=[(m.start(),stage) for stage,pattern in titles if (m:=re.search(pattern,head))]
    return min(matches)[1] if matches else '未分类'


def field_candidates(text,fields):
    """标签和材料类型共同限定字段；所有新增识别值均需人工确认。"""
    compact=compact_text(text);stage=material_stage(text);result=[]
    def add(column,value,evidence,value_type=None):
        if value is not None and value!='':result.append(dict(column=column,value=value,evidence=evidence,type=value_type,material_stage=stage))
    raw_codes=re.findall(PATTERNS['project_code'],compact)
    for code in fields.get('project_code',[]):add(25,canonical_code(code),next((raw for raw in raw_codes if canonical_code(raw)==canonical_code(code)),code))
    if stage=='未分类':return result
    for match in re.finditer(r'(?:建设单位名称|建设单位|单位名称)[:：]([^，,。；;:：]{2,80}?)(?=项目名称|项目代码|建设地点|项目拟选位置|拟用地面积|用地面积|拟建设规模|项目建设依据|证书文号|发证日期|[，,。；;]|$)',compact):add(26,match[1],match[0])
    area_labels={'预审与选址':r'拟用地面积|项目用地|用地面积','规划条件与红线':r'用地面积|地块面积','用地报批':r'批准用地面积|批准建设用地面积','土地供应':r'宗地面积|供地面积'}
    for m in re.finditer(r'(?:'+area_labels[stage]+r')[:：]?(?:约|为|共)?(\d+(?:\.\d+)?)(万平方米|平方米|公顷|亩|㎡|M2)',compact):
        area=float(m[1])
        if not math.isfinite(area) or area<=0:continue
        column,unit={'预审与选址':(32,37),'规划条件与红线':(40,46),'用地报批':(48,54),'土地供应':(61,62)}[stage]
        add(column,area,m[0]);add(unit,'平方米' if m[2]=='M2' else m[2],m[0])
        if stage=='预审与选址':add(31,m[0],m[0])
    patterns={
        '预审与选址':[(35,r'(?:证书文号|证书编号)[:：]([^，,。；;:：]{3,50})(?=[，,。；;]|$)'),(35,r'(用字第'+CERTIFICATE_NUMBER+r')')],
        '规划条件与红线':[(43,r'(?:出具编号|规划条件编号|条件编号)[:：]([^，,。；;:：]{3,50})(?=[，,。；;]|$)'),(39,r'(?:地块编码|地块编号)[:：]([A-Z0-9-]{3,40})(?![A-Z0-9-])')],
        '用地报批':[(52,r'(?:批复文号|批准文号)[:：]([^，,。；;:：]{3,50})(?=[，,。；;]|$)')],
        '土地供应':[(67,r'(?:合同编号|划拨决定书编号|决定书编号)[:：]([A-Z0-9-]{5,40})')],
    }
    for col,pattern in patterns[stage]:
        for m in re.finditer(pattern,compact):add(col,m[1],m[0])
    labels={
        '预审与选址':[(30,'建设地点|项目拟选位置|拟选位置'),(33,'项目规模及内容|拟建设规模'),(34,'项目建设依据')],
        '规划条件与红线':[(41,'用地性质'),(42,'用地位置'),(45,'出具依据')],
        '用地报批':[(47,'土地用途')],
        '土地供应':[(56,'土地受让人|受让人'),(57,'土地用途'),(59,'建设位置|宗地位置|土地坐落'),(63,'供地方式'),(65,'用地划拨方案批复文号|用地出让方案批复文号'),(66,'土地权属来源'),(69,'供地单位|出让人')],
    }
    for col,label in labels[stage]:
        # 无可靠分隔符的整段 OCR 不做文本字段切分，避免把下一栏一起当作值。
        for m in re.finditer(r'(?:'+label+r')[:：]([^，,。；;:：]{2,160})(?=[，,。；;]|$)',compact):add(col,m[1],m[0])
    label,col={'预审与选址':('发证日期',36),'规划条件与红线':('出具日期',44),'用地报批':('批复日期|批准日期',53),'土地供应':('合同签订日期|签订日期|决定书日期',68)}[stage]
    for m in re.finditer(r'(?:'+label+r')[:：]?(20\d{2})年(\d{1,2})月(\d{1,2})日',compact):
        try:value=dt.date(int(m[1]),int(m[2]),int(m[3])).isoformat()
        except ValueError:continue
        add(col,value,m[0],'date')
    return result
