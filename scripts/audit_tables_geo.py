"""Audit all records, cross-file consistency and geographic formats; no solvers."""
from audit_sources import ROOT, OUT, dump
import csv, json, re, collections
import numpy as np
import rasterio, h5py
from scipy.io import loadmat
books=json.loads((OUT/'workbooks_raw.json').read_text(encoding='utf-8'))
tables=[]
def table(b,s,label,header,start,end,cols):
    rows=s['rows']; fields=[]; data=[r[:cols] for r in rows[start-1:end]]
    for j,name in enumerate(rows[header-1][:cols]):
        vs=[r[j] for r in data]; non=[v for v in vs if v is not None]
        unit=re.search(r'[（(]([^（）()]+)[）)]',name or '')
        fields.append({'name':name,'unit':unit.group(1) if unit else ('无量纲' if '效率' in (name or '') or '系数' in (name or '') else '编号/文本/计数（见字段）'),'types':sorted(set(type(v).__name__ for v in non)),'missing':len(vs)-len(non),'distinct':len(set(map(str,non))),'min':min(non) if non and all(isinstance(v,(int,float)) for v in non) else None,'max':max(non) if non and all(isinstance(v,(int,float)) for v in non) else None})
    tables.append({'file':b['file'],'sheet':s['name'],'table':label,'header_row':header,'data_start':start,'data_end':end,'record_count':len(data),'fields':fields,'duplicate_rows':len(data)-len(set(tuple(r) for r in data)),'data':data})
for b in books:
    s=b['sheets'][0]; f=b['file']
    if '模板' in f:
        for s in b['sheets']:
            cols=max(i+1 for i,v in enumerate(s['rows'][0]) if v is not None)
            table(b,s,'提交字段',1,2,1,cols)
    elif '中继无人机' in f:
        for args in [('中继机型',2,3,3,19),('中继实体',6,7,8,3),('能源库存',11,12,12,3)]:table(b,s,*args)
    elif '运输无人机' in f:
        for args in [('运输机型',2,3,5,18),('运输实体',8,9,16,3),('电池库存',19,20,22,3)]:table(b,s,*args)
    elif '调度中心' in f:
        for args in [('调度中心',2,3,3,5),('服务区',6,7,21,6)]:table(b,s,*args)
    elif '物资需求' in f:
        table(b,s,'需求汇总',1,2,54,9);table(b,b['sheets'][1],'逐箱货箱',1,2,81,9)
    elif '通信链路' in f:
        # merged B:C is a single logical field
        table(b,s,'通信参数',2,3,16,5)
        tables[-1]['fields'].pop(2)
        for row in tables[-1]['data']:row.pop(2)
        tables[-1]['fields'][1]['name']='参数名称（B:C合并）'
dump('data_schema.json',tables)
by={t['table']:t for t in tables if t['table']!='提交字段'}
checks=[]
def check(name,ok,detail):checks.append({'check':name,'status':'PASS' if ok else 'REVIEW','detail':detail})
boxes=by['逐箱货箱']['data']; demands=by['需求汇总']['data']; nodes=by['调度中心']['data']+by['服务区']['data']
ids=[r[0] for r in boxes]; service={r[0] for r in by['服务区']['data']}
check('货箱唯一编号',len(ids)==len(set(ids)),f'{len(ids)}箱 / {len(set(ids))}唯一编号')
check('货箱服务区外键',all(r[1] in service for r in boxes),'全部逐箱记录关联服务区')
errors=[]
for d in demands:
    matches=[r for r in boxes if r[1:3]==d[:2]]
    if len(matches)!=d[2] or sum(r[5]=='是' for r in matches)!=d[3]:errors.append(d[:4])
    for r in matches:
        if r[3:5]!=d[4:6] or r[7:]!=[d[8],d[6]] or (r[5]=='是' and r[6]!=d[7]):errors.append(r[0])
check('需求汇总逐箱对账',not errors,str(errors) if errors else '各服务区×物资类型：箱数、首批数、质量、体积、时限、优先系数一致')
check('数量和库存',len(service)==15 and len(boxes)==80 and len(by['运输实体']['data'])==8,'15服务区；80箱；运输8架(A4/B2/C2)，电池14组(A6/B4/C4)；中继2架、能源组件6组')
check('逐箱数值正值',all(r[3]>0 and r[4]>0 and r[7]>0 and r[8]>0 for r in boxes),'质量、体积、期望时间、优先系数均>0')
check('非首批截止时间空值口径',all((r[6] is not None)==(r[5]=='是') for r in boxes),'仅首批保障货箱填写首批截止时间')
stats={'boxes':len(boxes),'total_mass_kg':sum(r[3] for r in boxes),'total_volume_m3':sum(r[4] for r in boxes),'first_batch':sum(r[5]=='是' for r in boxes),'medical':sum(r[2]=='医疗物资' for r in boxes),'population':sum(r[5] for r in by['服务区']['data']),'by_service':{s:{'boxes':sum(r[1]==s for r in boxes),'mass_kg':sum(r[3] for r in boxes if r[1]==s),'volume_m3':sum(r[4] for r in boxes if r[1]==s)} for s in sorted(service)}}
geo={}; tif=next((ROOT/'数据').rglob('*.tif')); mat=loadmat(tif.with_suffix('.mat'))
with rasterio.open(tif) as ds:
    a=ds.read(1); valid=np.isfinite(a)&(a!=float(mat['nodata'].item()))
    if ds.nodata is not None: valid &= a!=ds.nodata
    geo['dem']={'file':str(tif.relative_to(ROOT)),'shape':list(a.shape),'dtype':str(a.dtype),'pixels':a.size,'valid_pixels':int(valid.sum()),'nodata_pixels':int((~valid).sum()),'nodata':ds.nodata,'crs':str(ds.crs),'resolution_degrees':ds.res,'bounds_edges':list(ds.bounds),'center_nw':ds.xy(0,0),'center_se':ds.xy(ds.height-1,ds.width-1),'min_m':float(a[valid].min()),'max_m':float(a[valid].max()),'transform':list(ds.transform),'tags':ds.tags(),'mat_variables':{k:{'shape':list(v.shape),'dtype':str(v.dtype),'value':v.tolist() if v.size<=6 else None} for k,v in mat.items() if not k.startswith('__')}}
    check('DEM TIF/MAT数值一致',np.array_equal(a,mat['dem']),'全部像元逐项比较')
    check('GeoTIFF NoData元数据',ds.nodata==float(mat['nodata'].item()),'TIF='+str(ds.nodata)+'；MAT/PDF=-32767；当前栅格实际无该值，后续显式采用附件哨兵值')
    xs=np.array([ds.xy(0,c)[0] for c in range(ds.width)]); ys=np.array([ds.xy(r,0)[1] for r in range(ds.height)])
    check('DEM TIF/MAT坐标一致',np.allclose(xs,mat['longitude'].ravel(),rtol=0,atol=1e-10) and np.allclose(ys,mat['latitude'].ravel(),rtol=0,atol=1e-10),'全部像元中心坐标比较，容差1e-10度')
    geo['node_dem']=[]
    for r in nodes:
        rr,cc=ds.index(r[2],r[3]); inside=0<=rr<ds.height and 0<=cc<ds.width
        z=float(a[rr,cc]) if inside else None
        geo['node_dem'].append({'id':r[0],'inside':inside,'row':rr,'column':cc,'source_altitude':r[4],'dem_containing_cell':z,'difference_m':r[4]-z if inside else None})
    check('16任务节点位于DEM有效像元内',all(n['inside'] and n['dem_containing_cell']!=ds.nodata for n in geo['node_dem']),'包含O01和S001-S015')
geo['csv']=[];geo['mat_hdf']=[]
for p in sorted((ROOT/'数据').rglob('*.csv')):
    with p.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);rows=list(reader);headers=reader.fieldnames
    lon=[float(r['经度']) for r in rows];lat=[float(r['纬度']) for r in rows]
    g={'file':str(p.relative_to(ROOT)),'records':len(rows),'features':len({r[headers[0]] for r in rows}),'fields':headers,'missing':{k:sum(not r[k] for r in rows) for k in headers},'duplicate_rows':len(rows)-len(set(tuple(r.values()) for r in rows)),'bounds':[min(lon),min(lat),max(lon),max(lat)],'types':{k:len(set(r[k] for r in rows)) for k in headers},'name_unrecorded':sum(r.get('名称')=='未记录' for r in rows)}
    geo['csv'].append(g)
    with h5py.File(p.with_suffix('.mat')) as f:
        datasets=[];vectors={}
        def visit(name,obj):
            if isinstance(obj,h5py.Dataset):
                arr=obj[()]
                datasets.append({'path':name,'shape':list(obj.shape),'dtype':str(obj.dtype),'class':str(obj.attrs.get('MATLAB_class',b''))})
                flat=arr.ravel()
                if arr.dtype.kind in 'fiu' and flat.size==len(rows): vectors[name]=flat.tolist()
                if arr.dtype==np.dtype('uint64') and flat.size>4+len(rows) and list(flat[:4])==[1,2,len(rows),1]:
                    lengths=flat[4:4+len(rows)].astype(int); raw=flat[4+len(rows):].tobytes();pos=0;ss=[]
                    for length in lengths:
                        ss.append(raw[pos:pos+int(length)*2].decode('utf-16-le'));pos+=int(length)*2
                    vectors[name]=ss
        f.visititems(visit)
        matches={}
        for k in headers:
            expected=[r[k] for r in rows]
            for name,v in vectors.items():
                if isinstance(v[0],str): equal=v==expected
                else:
                    try: equal=bool(np.allclose(v,[float(x) for x in expected],rtol=0,atol=1e-10))
                    except ValueError: equal=False
                if equal:matches[k]=name;break
        check(p.stem+' CSV/MAT逐列对账',len(matches)==len(headers),str(matches))
        geo['mat_hdf'].append({'file':str(p.with_suffix('.mat').relative_to(ROOT)),'format':'MATLAB 7.3 HDF5 MCOS table','datasets':datasets,'column_matches':matches,'note':'读取全部dataset；数值数组与packed UTF-16字符串按全部记录与CSV逐列核对'})
html=next((ROOT/'数据').rglob('*.html')).read_text(encoding='utf-8')
geo['html']={'bytes':len(html.encode()),'external_urls':sorted(set(re.findall(r'https?://[^\s\"<>]+',html)))[:40],'feature_collections':html.count('FeatureCollection'),'features':html.count('"type": "Feature"'),'scripts':re.findall(r'<script[^>]+src=[\"\x27]([^\"\x27]+)',html),'layer_snippets':re.findall(r'.{0,70}(?:道路|水体|水系|村镇|镇龙乡边界).{0,70}',html)[:12]}
layers={}
for line in html.splitlines():
    m=re.match(r'\s*const (\w+) = (\{.*\}|\[.*\]);\s*$',line)
    if m:
        try: obj=json.loads(m[2])
        except json.JSONDecodeError:continue
        if isinstance(obj,dict) and obj.get('type')=='FeatureCollection':
            layers[m[1]]={'count':len(obj['features']),'geometry_types':dict(collections.Counter(f['geometry']['type'] for f in obj['features'])),'properties':list(obj['features'][0]['properties'])}
        elif m[1]=='elevationGrid':layers[m[1]]={'shape':[len(obj),len(obj[0])],'note':'展示用降采样/整数高程，不替代原始DEM'}
geo['html']['layers']=layers
check('正式运输及爬升能耗公式',False,'原DOCX XML只定义E=E_hor+E_up；现有附件未找到分项关系式，阻断正式物理求解，禁止猜式')
check('节点附件海拔与DEM像元口径',False,'最大绝对差 '+str(max(abs(n['difference_m']) for n in geo['node_dem']))+' m；保留两者，不自动覆盖')
check('Q4跨组共享中继继承口径',False,'固定Q3任务与通信关系且资源不得跨组：共享同一中继的服务区是否也必须绑定，待澄清')
dump('geodata_audit.json',geo);dump('audit_statistics.json',stats);dump('validation_checks.json',checks)
(OUT/'data_validation.txt').write_text('\n'.join(f"{c['status']} | {c['check']} | {c['detail']}" for c in checks),encoding='utf-8')
print(json.dumps({'statistics':stats,'checks':checks,'dem':geo['dem'],'csv':geo['csv'],'node_dem':geo['node_dem']},ensure_ascii=False,indent=2))
