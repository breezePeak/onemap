"""复用本机 QGIS 的 PROJ/GDAL；所有转换仅写工作副本。"""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import shutil
from shapely import make_valid
from shapely.geometry import shape,mapping
from shapely.ops import transform,polygonize

_dlls=[]

def gdal_task(mode,source):
    roots=[*Path('D:/Program Files').glob('QGIS *'),*Path('C:/Program Files').glob('QGIS *')]
    if not roots:raise RuntimeError('未找到本机QGIS运行库')
    root=roots[0];env=os.environ.copy();env['PYTHONHOME']=str(root/'apps/Python312');env['PYTHONPATH']=str(root/'apps/Python312/Lib/site-packages')
    env['GDAL_DATA']=str(root/'apps/gdal/share/gdal');env['PROJ_DATA']=str(root/'share/proj')
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'work') as folder:
        output=Path(folder)/'result.json'
        result=subprocess.run([str(root/'bin/python.exe'),str(Path(__file__).with_name('gdal_worker.py')),mode,str(Path(source).resolve()),str(output)],env=env,capture_output=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not output.exists():raise RuntimeError('图形读取失败：'+result.stderr.decode('utf-8',errors='replace')[-2000:])
        return json.loads(output.read_text(encoding='utf-8'))

def runtime():
    try:
        from pyproj import CRS,Transformer
    except ImportError:
        roots=[*Path('D:/Program Files').glob('QGIS *'),*Path('C:/Program Files').glob('QGIS *')]
        for root in roots:
            site=root/f'apps/Python{sys.version_info.major}{sys.version_info.minor}/Lib/site-packages'
            if not site.exists():continue
            sys.path.append(str(site))
            if os.name=='nt':_dlls.append(os.add_dll_directory(str(root/'bin')))
            os.environ.setdefault('PROJ_DATA',str(root/'share/proj'))
            os.environ.setdefault('GDAL_DATA',str(root/'apps/gdal/share/gdal'))
        from pyproj import CRS,Transformer
    return CRS,Transformer

def projected_crs(value):
    CRS,_=runtime();crs=CRS.from_user_input(value)
    if not crs.is_projected or any(abs(a.unit_conversion_factor-1)>1e-9 for a in crs.axis_info[:2]):
        raise ValueError('参考坐标系必须为以米为单位的投影坐标系，才能计算平方米面积')
    return crs

def reproject(geometry,source,target):
    CRS,Transformer=runtime();a,b=CRS.from_user_input(source),CRS.from_user_input(target)
    if a==b:return make_valid(geometry)
    converter=Transformer.from_crs(a,b,always_xy=True,allow_ballpark=False,only_best=True)
    result=transform(lambda x,y,z=None:converter.transform(x,y,errcheck=True),geometry)
    if result.is_empty:raise ValueError('坐标转换后图形为空')
    return make_valid(result)

def cad_entities(path):
    if Path(path).suffix.lower()=='.dwg':
        converters=list((Path(__file__).resolve().parent.parent/'work/cad_runtime').rglob('dwg2dxf.exe'))
        if not converters:raise ValueError('缺少已校验的LibreDWG转换器')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'work') as folder:
            original=Path(folder)/'source.dwg';output=Path(folder)/'source.dxf';shutil.copyfile(path,original)
            result=subprocess.run([str(converters[0]),'-v0','--as','r2000','-o',str(output),str(original)],capture_output=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode or not output.is_file():raise ValueError('DWG转换失败：'+result.stderr.decode('utf-8',errors='replace')[-1500:])
            return cad_entities(output)
    entities=gdal_task('cad',path);polygons=[];lines={}
    for entity in entities:
        attrs=entity['attributes'];geometry=shape(entity['geometry'])
        if geometry.geom_type in ('Polygon','MultiPolygon'):polygons.append((attrs,make_valid(geometry)))
        elif geometry.geom_type in ('LineString','MultiLineString'):lines.setdefault(entity['layer'],[]).append(geometry)
    for layer,geometries in lines.items():
        for polygon in polygonize(geometries):polygons.append((dict(Layer=layer,边界来源='同CAD图层闭合线'),make_valid(polygon)))
    return entities,polygons
