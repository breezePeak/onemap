"""识别后的标识和同字节附件驱动下一轮检索，不直接回填推断值。"""
import json
import os
from pathlib import Path
import subprocess
import sys
from extract_pdfs import main as extract


def main():
    base=Path(__file__).resolve().parent
    node=Path(os.environ['USERPROFILE'])/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    skip_ocr='--skip-ocr' in sys.argv
    def run(script,*args):
        command=[str(node) if script.endswith('.mjs') else 'powershell' if script.endswith('.ps1') else sys.executable]
        if script.endswith('.ps1'):command+=['-NoProfile','-ExecutionPolicy','Bypass','-File']
        subprocess.run([*command,str(base/script),*args],stdout=sys.stdout,stderr=subprocess.STDOUT,check=True,timeout=3600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def sources():
        docs=json.loads(Path('work/pdf_documents.json').read_text(encoding='utf-8'))
        images=json.loads(Path('work/ocr_manifest.json').read_text(encoding='utf-8'))
        return {d['source'] for d in docs}|{x['source'] for x in images if '/pdf_pages/' not in Path(x['path']).as_posix()}
    history=[];converged=False
    def relationships():
        path=Path('outputs/governance/识别字段候选.jsonl')
        if not path.exists():return set()
        return {json.dumps({k:item.get(k) for k in ('source','projects','pending_projects','fields','identity_conflicts')},sort_keys=True,ensure_ascii=False) for line in path.read_text(encoding='utf-8').splitlines() if line.strip() for item in [json.loads(line)]}
    # shortcut: 最多扩展三轮，仍有新增时在统计中提示；大规模关联图需改为增量任务队列。
    for round_no in range(1,4):
        before=sources();old_relationships=relationships();after=extract();added=after-before
        print('跨阶段扩展轮次',round_no,'新增材料',len(added),flush=True)
        history.append(dict(round=round_no,new_materials=len(added),total_materials=len(after)))
        # extract 重建了渲染清单，即使无新增也需恢复页面元数据。
        run('render_pdfs.mjs')
        if not skip_ocr:run('ocr_images.ps1','-Manifest',str(Path('work/ocr_manifest.json').resolve()))
        run('extract_fields.py')
        run('spatial_matches.py')
        changed=relationships()!=old_relationships
        history[-1]['relationships_changed']=changed
        if not added and not changed:converged=True;break
    Path('outputs/governance/跨阶段检索统计.json').write_text(json.dumps(dict(supply_first=True,rounds=history,converged=converged,ocr_skipped=skip_ocr),ensure_ascii=False,indent=2),encoding='utf-8')
    if not converged:print('材料或关联关系仍有变化，已达三轮扩展上限，可重新检索继续补充。',flush=True)


if __name__=='__main__':main()
