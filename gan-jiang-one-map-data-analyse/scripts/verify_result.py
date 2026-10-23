"""逐格核对：只允许声明的空白补丁，原值、公式、格式和表结构保持。"""
import datetime
from copy import copy
import hashlib
import json
from pathlib import Path
import openpyxl
from preserve_original_package import cell_colors

def main():
    source=Path('test_result.xlsx');result=Path('outputs/governance/test_result_已填充.xlsx')
    original=openpyxl.load_workbook(source);filled=openpyxl.load_workbook(result)
    patches={x['cell']:x for x in json.loads(Path('work/patches.json').read_text(encoding='utf-8'))}
    colors=cell_colors(list(patches.values()))
    assert original.sheetnames==filled.sheetnames,'工作表发生变化'
    changes=[]
    for a,b in zip(original,filled):
        assert str(a.merged_cells)==str(b.merged_cells),'合并单元格发生变化'
        assert a.freeze_panes==b.freeze_panes,'冻结窗格发生变化'
        assert a.auto_filter.ref==b.auto_filter.ref,'筛选范围发生变化'
        assert a.max_row==b.max_row and a.max_column==b.max_column,'数据范围发生变化'
        assert str(a.data_validations)==str(b.data_validations),'数据验证发生变化'
        for key,dim in a.column_dimensions.items():
            other=b.column_dimensions[key]
            assert dim.width==other.width and dim.hidden==other.hidden,(a.title,key,'列宽或隐藏状态变化')
        for key,dim in a.row_dimensions.items():
            other=b.row_dimensions[key]
            assert dim.height==other.height and dim.hidden==other.hidden,(a.title,key,'行高或隐藏状态变化')
        coordinates=set(a._cells)|set(b._cells)
        for coord in coordinates:
            old=a.cell(*coord);new=b.cell(*coord)
            expected=patches.get(old.coordinate) if a.title=='Sheet1' else None
            if old.value!=new.value:
                assert expected is not None,(a.title,old.coordinate,'未授权修改')
                assert old.value is None or str(old.value).strip()=='' or expected.get('allow_overwrite'),'覆盖了已有值'
                value=datetime.datetime.fromisoformat(expected['value']) if expected.get('type')=='date' else expected['value']
                assert new.value==value,(old.coordinate,new.value,value)
                changes.append(old.coordinate)
            assert all(copy(getattr(old,k))==copy(getattr(new,k)) for k in ('font','border','alignment','protection')),(a.title,old.coordinate,'单元格格式改变')
            color=colors.get(old.coordinate) if a==original.worksheets[0] else None
            if color:assert new.fill.fgColor.rgb==color,(old.coordinate,'标色错误')
            else:assert copy(old.fill)==copy(new.fill),(old.coordinate,'未授权填色')
            if not expected or expected.get('type')!='date':assert old.number_format==new.number_format,'未授权数字格式改变'
            assert old.comment==new.comment,'批注发生变化'
    assert set(changes).issubset(patches),'有未声明修改'
    for cell,p in patches.items():
        value=datetime.datetime.fromisoformat(p['value']) if p.get('type')=='date' else p['value']
        assert filled.worksheets[0][cell].value==value,(cell,'补丁未落地')
    report=dict(status='PASS',changed_cells=sorted(changes),filled_cells=len(changes),highlighted_cells=len(colors),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),checks=['空白回填或用户明确选择','已有值及公式保留（用户选择除外）','表名/合并/范围/冻结/筛选/数据验证保留','行高列宽隐藏状态保留','仅授权单元格填色及日期格式变化','批注保留'])
    Path('outputs/governance/验证结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS',len(changes),'cells; original values and structure preserved')

if __name__=='__main__':main()
