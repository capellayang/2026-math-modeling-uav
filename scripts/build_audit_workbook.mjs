import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.join(root,'outputs','data_audit');
const read=async n=>JSON.parse(await fs.readFile(path.join(out,n),'utf8'));
const [schema,geo,stats,checks,manifest]=await Promise.all(['data_schema.json','geodata_audit.json','audit_statistics.json','validation_checks.json','source_manifest.json'].map(read));
const wb=Workbook.create();
const sheets=[
 ['审计概览',[['项目','数量或状态','说明'],['服务区',15,'O01另计'],['货箱',stats.boxes,'80个唯一编号'],['总质量（kg）',stats.total_mass_kg,'逐箱求和'],['总体积（m³）',stats.total_volume_m3,'逐箱求和'],['首批保障箱',stats.first_batch,'附件指定'],['医疗箱',stats.medical,'期望时间为硬约束'],['运输无人机',8,'A4 / B2 / C2'],['共享电池',14,'A6 / B4 / C4，含初装'],['中继无人机',2,'R01 / R02'],['能源组件',6,'共享库存'],['公式状态','待澄清','水平和爬升能耗分项缺式，见审计报告'],['原始依据','本地DOCX及附件','完整路径与SHA256见源文件页']]],
 ['输入表清单',[['文件','Sheet','逻辑表','表头行','数据起始行','数据结束行','记录数','字段数'],...schema.map(t=>[t.file,t.sheet,t.table,t.header_row,t.data_start,t.data_end,t.record_count,t.fields.length])]],
 ['字段字典',[['文件','Sheet / 逻辑表','字段','单位或口径','类型','缺失数','不同值数','最小值','最大值'],...schema.flatMap(t=>t.fields.map(f=>[t.file,t.sheet+' / '+t.table,f.name,f.unit,f.types.join('/'),f.missing,f.distinct,f.min,f.max]))]],
 ['地理数据',[['文件','记录数','要素数','字段与单位'],...geo.csv.map(g=>[g.file,g.records,g.features,g.fields.join('；')+'（经纬度为°，长度_km为km，编号及点序号无量纲）']),[geo.dem.file,geo.dem.pixels,1,'1309×1486 float32；EPSG:4326；高程m'],['DEM MAT',geo.dem.pixels,1,'dem / epsg_code / latitude / longitude / nodata / transform'],['HTML地图',8,8,'8个GeoJSON图层；159×180展示高程网格']]],
 ['节点高程对照',[['节点','附件海拔（m）','所在DEM像元（m）','附件减DEM（m）','栅格行','栅格列'],...geo.node_dem.map(n=>[n.id,n.source_altitude,n.dem_containing_cell,n.difference_m,n.row,n.column])]],
 ['核查记录',[['检查','状态','依据或详情'],...checks.map(c=>[c.check,c.status,c.detail])]],
 ['源文件',[['相对路径','字节数','SHA256'],...manifest.map(f=>[f.path,f.bytes,f.sha256])]]
];
for(const [name,rows] of sheets){
 const sh=wb.worksheets.add(name); const n=rows[0].length;
 sh.getRangeByIndexes(0,0,rows.length,n).values=rows;
 const all=sh.getRangeByIndexes(0,0,rows.length,n);
 all.format.font={name:'Microsoft YaHei',size:10};all.format.rowHeight=38;all.format.verticalAlignment='center';all.format.wrapText=true;
 sh.getRangeByIndexes(0,0,1,n).format={fill:'#24445C',font:{bold:true,color:'#FFFFFF'},rowHeight:32};
 sh.showGridLines=false;sh.freezePanes.freezeRows(1);
 for(let c=0;c<n;c++)sh.getRangeByIndexes(0,c,rows.length,1).format.columnWidth=[name==='字段字典'?40:46,28,52,44,14,14,14,16,16][c];
 if(name==='输入表清单'){for(let c=3;c<n;c++)sh.getRangeByIndexes(0,c,rows.length,1).format.columnWidth=14;}
 if(name==='字段字典')all.format.rowHeight=58;
 if(name==='地理数据'){all.format.rowHeight=80;sh.getRangeByIndexes(0,3,rows.length,1).format.columnWidth=85;}
 if(name==='核查记录'){all.format.rowHeight=72;sh.getRangeByIndexes(0,2,rows.length,1).format.columnWidth=100;}
 if(name==='源文件'){all.format.rowHeight=68;sh.getRangeByIndexes(0,0,rows.length,1).format.columnWidth=70;sh.getRangeByIndexes(0,2,rows.length,1).format.columnWidth=72;}
 if(name==='节点高程对照'){sh.getRange('B2:D17').setNumberFormat('0.0000');}
}
wb.recalculate();
for(const [name,rows] of sheets){
 const blob=await wb.render({sheetName:name,range:`A1:${String.fromCharCode(64+rows[0].length)}${Math.min(rows.length,8)}`,scale:1,format:'png'});
 await fs.writeFile(path.join(out,`preview_${name}.png`),new Uint8Array(await blob.arrayBuffer()));
}
console.log((await wb.inspect({kind:'table',range:'审计概览!A1:C13',include:'values',tableMaxRows:13,tableMaxCols:3})).ndjson);
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(out,'data_summary.xlsx'));
console.log('Saved data_summary.xlsx');
