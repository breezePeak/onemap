import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const out = 'outputs/governance';
await fs.mkdir(out, {recursive:true});
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(process.argv.includes('--verify-view') ? `${out}/test_result_已填充.xlsx` : 'test_result.xlsx'));
const sheet = wb.worksheets.getItem('Sheet1');
if (process.argv.includes('--verify-view')) {
  for (const [name,range] of [['values','X8:AF13'],['approvals','AS1:BA5']]) {
    const blob=await wb.render({sheetName:'Sheet1',range,scale:1.3});
    await fs.writeFile(`work/verified_${name}.png`,new Uint8Array(await blob.arrayBuffer()));
  }
} else if (process.argv.includes('--preview')) {
  console.log((await wb.inspect({kind:'table',range:'Sheet1!X1:AK7',include:'values,formulas',maxChars:2000,tableMaxRows:7,tableMaxCols:14})).ndjson);
  const blob = await wb.render({sheetName:'Sheet1',range:'X1:AK7',scale:1.3});
  await fs.writeFile('work/before.png',new Uint8Array(await blob.arrayBuffer()));
} else {
  const patches = JSON.parse(await fs.readFile('work/patches.json','utf8'));
  for (const p of patches) {
    const range=sheet.getRange(p.cell);
    if (!p.allow_overwrite && range.values[0][0]!==null && range.values[0][0]!==undefined && range.values[0][0]!=='') throw new Error(`Refuse to overwrite ${p.cell}`);
    range.values = [[p.type==='date' ? new Date(p.value+'T00:00:00Z') : p.value]];
    if(p.type==='date')range.setNumberFormat('yyyy-mm-dd');
  }
  wb.recalculate();
  console.log((await wb.inspect({kind:'table',range:'Sheet1!X3:AK13',include:'values,formulas',maxChars:2000,tableMaxRows:11,tableMaxCols:14})).ndjson);
  const blob = await wb.render({sheetName:'Sheet1',range:'X1:AK7',scale:1.3});
  await fs.writeFile('work/after.png',new Uint8Array(await blob.arrayBuffer()));
  await (await SpreadsheetFile.exportXlsx(wb)).save(`${out}/test_result_已填充.xlsx`);
}
