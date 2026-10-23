"""把 artifact-tool 生成的补丁单元格放回原 XLSX 包，保留其未支持的字体字符集等元数据。"""
from copy import deepcopy
import json
import csv
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from openpyxl.utils import column_index_from_string

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('',NS)
ET.register_namespace('r','http://schemas.openxmlformats.org/officeDocument/2006/relationships')
Q=lambda name:'{'+NS+'}'+name

COLORS={'filled':'FFDDF3E4','conflict':'FFFFE6B7','candidate':'FFFFE6B7','selected':'FFDDEAFE','kept':'FFDDEAFE'}

def cell_colors(patches):
    colors={p['cell']:COLORS['selected' if p.get('allow_overwrite') else 'filled'] for p in patches}
    states=Path('work/cell_states.json')
    if states.exists():
        for cell,status in json.loads(states.read_text(encoding='utf-8')).items():
            if status in COLORS:colors[cell]=COLORS[status]
    else:
        conflict_file=Path('outputs/governance/已有值冲突.csv')
        if conflict_file.exists():
            with conflict_file.open(encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):colors[row['cell']]=COLORS['conflict']
    return colors

def main():
    target=Path('outputs/governance/test_result_已填充.xlsx')
    patches=json.loads(Path('work/patches.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile('test_result.xlsx') as source,zipfile.ZipFile(target) as exported:
        original=ET.fromstring(source.read('xl/worksheets/sheet1.xml'))
        author=ET.fromstring(exported.read('xl/worksheets/sheet1.xml'))
        styles=ET.fromstring(source.read('xl/styles.xml'))
        strings=ET.fromstring(exported.read('xl/sharedStrings.xml')) if 'xl/sharedStrings.xml' in exported.namelist() else []
        authored={c.attrib['r']:c for c in author.iter(Q('c'))}
        cells={c.attrib['r']:c for c in original.iter(Q('c'))}
        rows={int(r.attrib['r']):r for r in original.iter(Q('row'))}
        xfs=styles.find(Q('cellXfs'));numfmts=styles.find(Q('numFmts'))
        if numfmts is None:numfmts=ET.Element(Q('numFmts'),count='0');styles.insert(0,numfmts)
        format_id=max([163]+[int(x.attrib['numFmtId']) for x in numfmts])+1
        colors=cell_colors(patches);fills=styles.find(Q('fills'));fill_ids={}
        for color in set(colors.values()):
            fill_ids[color]=str(len(fills));fill=ET.SubElement(fills,Q('fill'))
            pattern=ET.SubElement(fill,Q('patternFill'),patternType='solid')
            ET.SubElement(pattern,Q('fgColor'),rgb=color);ET.SubElement(pattern,Q('bgColor'),indexed='64')
        fills.set('count',str(len(fills)))
        new_styles={};by_cell={p['cell']:p for p in patches}
        for coord in set(by_cell)|set(colors):
            patch=by_cell.get(coord,{});old=cells.get(coord)
            new=deepcopy(authored[coord]) if patch else deepcopy(old) if old is not None else ET.Element(Q('c'),r=coord)
            assert not patch or patch.get('allow_overwrite') or old is None or (old.find(Q('v')) is None and old.find(Q('is')) is None and old.find(Q('f')) is None),'拒绝覆盖已有值'
            style_id=old.attrib.get('s','0') if old is not None else '0'
            style_key=(style_id,patch.get('type'),colors.get(coord))
            if style_key not in new_styles:
                xf=deepcopy(xfs[int(style_id)])
                if patch.get('type')=='date':xf.set('numFmtId',str(format_id));xf.set('applyNumberFormat','1')
                if coord in colors:xf.set('fillId',fill_ids[colors[coord]]);xf.set('applyFill','1')
                new_styles[style_key]=str(len(xfs));xfs.append(xf)
            style_id=new_styles[style_key]
            new.set('s',style_id)
            if patch and new.attrib.get('t')=='s':
                index=int(new.find(Q('v')).text);si=strings[index]
                new.remove(new.find(Q('v')));new.set('t','inlineStr')
                inline=ET.SubElement(new,Q('is'))
                for element in si:inline.append(deepcopy(element))
            row=rows[int(''.join(c for c in coord if c.isdigit()))]
            if old is not None:row.remove(old)
            row.append(new)
            row[:]=sorted(row,key=lambda c:column_index_from_string(''.join(x for x in c.attrib['r'] if x.isalpha())))
        replacements={'xl/worksheets/sheet1.xml':ET.tostring(original,encoding='utf-8',xml_declaration=True)}
        if new_styles:
            if any(p.get('type')=='date' for p in patches):ET.SubElement(numfmts,Q('numFmt'),numFmtId=str(format_id),formatCode='yyyy-mm-dd')
            numfmts.set('count',str(len(numfmts)));xfs.set('count',str(len(xfs)))
            replacements['xl/styles.xml']=ET.tostring(styles,encoding='utf-8',xml_declaration=True)
        temporary=target.with_suffix('.tmp.xlsx')
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as final:
            for info in source.infolist():final.writestr(info,replacements.get(info.filename,source.read(info.filename)))
    temporary.replace(target)
    print('Preserved original XLSX package; replaced only declared cells and added date formats')

if __name__=='__main__':main()
