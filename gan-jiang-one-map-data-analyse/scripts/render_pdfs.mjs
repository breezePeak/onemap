import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { createCanvas } from '@napi-rs/canvas';
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs';

const require=createRequire(import.meta.url);
const pdfRoot=path.dirname(require.resolve('pdfjs-dist/package.json'));
// M1 uses this same renderer for one bounded page, without running the full manifest.
if(process.argv[2]==='--page'){
  const [source,number,destination]=process.argv.slice(3);
  const pdf=await getDocument({data:new Uint8Array(await fs.readFile(source)),useSystemFonts:true,wasmUrl:path.join(pdfRoot,'wasm').replaceAll('\\','/')+'/',standardFontDataUrl:path.join(pdfRoot,'standard_fonts').replaceAll('\\','/')+'/',verbosity:0}).promise;
  const page=await pdf.getPage(Number(number)),size=page.getViewport({scale:1});
  const viewport=page.getViewport({scale:Math.min(2.5,2500/Math.max(size.width,size.height))});
  const canvas=createCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));
  await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
  await fs.writeFile(destination,canvas.toBuffer('image/png'));await pdf.destroy();
  process.exit(0);
}
const documents=JSON.parse(await fs.readFile('work/pdf_documents.json','utf8'));
const force=process.argv.includes('--force');
const workerIndex=process.argv.indexOf('--document');
const fragmentPath=i=>`work/render_manifests/${createHash('sha256').update(documents[i].source).digest('hex').slice(0,24)}.json`;
await fs.mkdir('work/render_manifests',{recursive:true});
if(workerIndex!==-1){
  const i=Number(process.argv[workerIndex+1]),d=documents[i];
  const stat=await fs.stat(d.path);
  const previous=await fs.readFile(fragmentPath(i),'utf8').then(JSON.parse).catch(()=>null);
  const refresh=force || (previous?.source===d.source && (previous.size!==stat.size || previous.mtimeMs!==stat.mtimeMs));
  const record={source:d.source,size:stat.size,mtimeMs:stat.mtimeMs,complete:false,pages:[]};
  const save=()=>fs.writeFile(fragmentPath(i),JSON.stringify(record));
  await save();
  const pdf=await getDocument({data:new Uint8Array(await fs.readFile(d.path)),useSystemFonts:true,wasmUrl:path.join(pdfRoot,'wasm').replaceAll('\\','/')+'/',standardFontDataUrl:path.join(pdfRoot,'standard_fonts').replaceAll('\\','/')+'/',verbosity:0}).promise;
  record.totalPages=pdf.numPages;
  const folder=path.resolve(process.env.GOV_ASSETS || 'work','pdf_pages',d.project);await fs.mkdir(folder,{recursive:true});
  for(let n=1;n<=pdf.numPages;n++){
    const imagePath=path.join(folder,(d.cache_key || path.basename(d.path,'.pdf'))+`_p${n}.png`);
    const imageStat=await fs.stat(imagePath).catch(()=>null);
    if(refresh || !imageStat || imageStat.mtimeMs<stat.mtimeMs){
      const page=await pdf.getPage(n),size=page.getViewport({scale:1});
      const viewport=page.getViewport({scale:Math.min(2.5,2500/Math.max(size.width,size.height))});
      const canvas=createCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));
      await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;
      await fs.writeFile(imagePath,canvas.toBuffer('image/png'));page.cleanup();
      await fs.rm(imagePath.replace(/\.png$/,'.txt'),{force:true});
    }
    record.pages.push({project:d.project,retrieval_projects:d.retrieval_projects || [d.project],storage_stage:d.storage_stage,path:imagePath,text:imagePath.replace(/\.png$/,'.txt'),source:d.source+`#page=${n}`});
    await save();
  }
  await pdf.destroy();record.complete=true;await save();
}else{
  const manifest=JSON.parse(await fs.readFile('work/ocr_manifest.json','utf8')).filter(x=>!x.path.replaceAll('\\','/').includes('/pdf_pages/'));
  const errors=[];let count=0;
  for(let i=0;i<documents.length;i++){
    const d=documents[i],stat=await fs.stat(d.path);
    let record=await fs.readFile(fragmentPath(i),'utf8').then(JSON.parse).catch(()=>null);
    let reusable=!force && (record?.complete || record?.error) && record.source===d.source && record.size===stat.size && record.mtimeMs===stat.mtimeMs;
    if(reusable)reusable=(await Promise.all(record.pages.map(p=>fs.stat(p.path).catch(()=>null)))).every(Boolean);
    if(!reusable){
      let failure;
      try{
        await promisify(execFile)(process.execPath,[fileURLToPath(import.meta.url),'--document',String(i),...(force?['--force']:[])],{timeout:90000,maxBuffer:1024*1024,windowsHide:true});
      }catch(error){
        failure=error.killed?'PDF页面渲染超过90秒，已保留完成页，其余待复核':String(error.stderr || error.message).slice(-2000);
      }
      record=await fs.readFile(fragmentPath(i),'utf8').then(JSON.parse).catch(()=>null);
      if(failure){
        record=record?.source===d.source?record:{source:d.source,size:stat.size,mtimeMs:stat.mtimeMs,complete:false,pages:[]};
        record.error=failure;await fs.writeFile(fragmentPath(i),JSON.stringify(record));
      }
    }
    if(record?.error)errors.push({source:d.source,error:record.error});
    if(record?.source===d.source){manifest.push(...record.pages.map(page=>({...page,project:d.project,retrieval_projects:d.retrieval_projects || [d.project],storage_stage:d.storage_stage})));count+=record.pages.length;}
    if((i+1)%10===0)console.log(`PDF rendering ${i+1}/${documents.length}; ${count} pages; ${errors.length} pending files`);
  }
  await fs.writeFile('work/ocr_manifest.json',JSON.stringify([...new Map(manifest.map(x=>[x.source,x])).values()]));
  await fs.writeFile('work/pdf_render_errors.json',JSON.stringify(errors));
  console.log(`Rendered ${count} PDF pages; ${errors.length} files need review`);
}
