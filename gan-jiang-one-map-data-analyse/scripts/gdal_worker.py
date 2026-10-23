"""GDAL在QGIS自带Python中运行，隔离主程序的DLL及NumPy版本。"""
import json
import os
from pathlib import Path
import sys

if __name__=='__main__':
    root=Path(sys.executable).parent
    dll=os.add_dll_directory(str(root)) if os.name=='nt' else None
    from osgeo import ogr,gdal
    gdal.UseExceptions();ogr.UseExceptions()
    mode,source,destination=sys.argv[1:]
    if mode=='cad':
        dataset=ogr.Open(source)
        if dataset is None:raise ValueError('CAD/DXF无法读取：'+gdal.GetLastErrorMsg())
        result=[];warnings=[]
        for layer in dataset:
            while True:
                try:feature=layer.GetNextFeature()
                except RuntimeError as error:
                    warnings.append(str(error));break
                if feature is None:break
                if len(result)>=100000:raise ValueError('CAD超过10万实体，请分图处理')
                geometry=feature.GetGeometryRef()
                if geometry is not None:result.append(dict(id=feature.GetFID(),layer=feature.items().get('Layer') or layer.GetName(),attributes=feature.items(),geometry=json.loads(geometry.GetLinearGeometry().ExportToJson())))
        if warnings:
            if not result:raise ValueError('未获得可用实体：'+'；'.join(warnings))
            result[0]['read_warning']='；'.join(warnings)
    elif mode=='mask':
        raster=gdal.Open(source)
        if raster is None:raise ValueError('边界掩膜无法读取')
        band=raster.GetRasterBand(1);vectors=ogr.GetDriverByName('Memory').CreateDataSource('');layer=vectors.CreateLayer('boundary',geom_type=ogr.wkbPolygon);layer.CreateField(ogr.FieldDefn('value',ogr.OFTInteger))
        if gdal.Polygonize(band,band,layer,0,[])!=0:raise ValueError('闭合区域提取失败')
        result=[json.loads(f.GetGeometryRef().ExportToJson()) for f in layer]
    else:raise ValueError('未知GDAL任务')
    Path(destination).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
