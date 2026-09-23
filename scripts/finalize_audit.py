"""Generate readable field catalog and scaffold; never edit original attachments."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/data_audit'
schema=json.loads((OUT/'data_schema.json').read_text(encoding='utf-8'))
geo=json.loads((OUT/'geodata_audit.json').read_text(encoding='utf-8'))
lines=['# 输入字段与提交模板','', '字段原文保留。单位从字段括号/附件定义读取；编号和文本不赋物理单位。数据类型为实际Excel单元格类型。完整记录见outputs/data_audit/workbooks_raw.json。','']
for t in schema:
    lines += [f"## {t['file']} / {t['sheet']} / {t['table']}",'',f"表头第{t['header_row']}行；{t['record_count']}条数据；{len(t['fields'])}个逻辑字段。",'', '| 字段 | 单位/含义 | 类型 | 缺失 | 数值范围 |','|---|---|---|---:|---|']
    for f in t['fields']:
        lines.append(f"| {f['name']} | {f['unit']} | {'/'.join(f['types']) or '空白模板'} | {f['missing']} | {str(f['min'])+' ～ '+str(f['max']) if f['min'] is not None else '—'} |")
    lines+=['']
for g in geo['csv']:
    lines += [f"## {g['file']}",'',f"{g['records']}行，{g['features']}个要素；同名MAT逐列核对。",'', '| 字段 | 单位/类型 |','|---|---|']
    for name in g['fields']:
        meaning='° / 浮点数' if name in ['经度','纬度'] else 'km / 浮点数（每要素重复）' if name=='长度_km' else '无量纲 / 局部整数序号' if name in ['点序号','环编号','多边形编号'] else '标识符 / 应按字符串保留' if '编号' in name else '文本类别或名称'
        lines.append(f'| {name} | {meaning} |')
    lines+=['']
lines+=['## 单位换算与主键注意事项','','内部统一m、s、kg、m³、kWh；功率kW×s需除3600；通信距离单独转换成km；频率MHz；SOC用0–1而附件/模板用0–100。经纬度不能直接作为米。货箱编号为逐箱主键，服务区编号为外键；需求汇总主键为服务区×物资类型；资源型号与实体ID分离。地理数据主键含要素编号和局部点序号，水面还须多边形编号与环编号。','']
(ROOT/'docs/输入字段与模板.md').write_text('\n'.join(lines),encoding='utf-8')
for rel in ['configs','tests']+['src/relief_uav/'+x for x in ['data','geo','physics','scheduling','communication','q1','q2','q3','q4','validation','export']]+['outputs/'+x for x in ['cache','q1','q2','q3','q4','figures','validation','logs']]:
    d=ROOT/rel;d.mkdir(parents=True,exist_ok=True)
    if not any(d.iterdir()):(d/'.gitkeep').write_text('',encoding='utf-8')
import fitz
pdf=fitz.open(next((ROOT/'数据').rglob('*.pdf')))
pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(OUT/'geodata_description_page.png')
print('Field catalog and scaffold created; PDF page rendered.')
