"""仅提取目标项目的划拨扫描件；不解压整份 4 GB 档案。"""
import json
import hashlib
import os
import zipfile
from pathlib import Path
import openpyxl
from image_materials import prepare_image,valid_image,IMAGE_ERRORS
from inspect_sources import ROOT, OUT
from cross_stage_materials import contains_token,SENSITIVE
from extract_archives import RecordedZip
from link_stages import target_records

def main():
    projects=target_records()
    members=json.loads((OUT/'划拨_zip.json').read_text(encoding='utf-8'))
    manifest=[];errors=[]
    with RecordedZip(ROOT/'4.土地供应阶段/划拨.zip') as z:
        for member in members:
            if SENSITIVE.search(member['name']):continue
            matches=[t['project'] for t in projects if contains_token(member['name'],t['archive_id'])]
            if not matches or not member['name'].lower().endswith(('.jpg','.jpeg','.png','.bmp')):continue
            key=matches[0]; folder=Path(os.environ.get('GOV_ASSETS',str(OUT)))/'materials'/key; folder.mkdir(parents=True,exist_ok=True)
            dest=folder/(hashlib.sha256(member['name'].encode()).hexdigest()[:12]+'_'+Path(member['name']).name)
            source=str(ROOT/'4.土地供应阶段/划拨.zip')+'!'+member['name']
            try:
                if member.get('size')==0:raise ValueError('图片为空文件（0字节），无法识别')
                if not valid_image(dest) or dest.stat().st_mtime_ns < (ROOT/'4.土地供应阶段/划拨.zip').stat().st_mtime_ns:
                    with z.open(member['raw']) as stream:prepare_image(stream,dest)
                manifest.append({'project':key,'retrieval_projects':matches,'path':str(dest.resolve()),'text':str(dest.with_suffix('.txt').resolve()),'source':source})
            except (*IMAGE_ERRORS,zipfile.BadZipFile,NotImplementedError,RuntimeError) as error:
                errors.append(dict(source=source,projects=matches,error=type(error).__name__+': '+str(error)))
                print('扫描件未读取',source,str(error),flush=True)
    (OUT/'ocr_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    (OUT/'material_errors.json').write_text(json.dumps(errors,ensure_ascii=False,indent=2),encoding='utf-8')
    print('提取扫描件',len(manifest),'异常',len(errors),'；异常原件保留，其他材料继续')

if __name__=='__main__':main()
