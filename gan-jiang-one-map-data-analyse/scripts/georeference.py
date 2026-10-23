"""控制点仿射配准、误差检查及红线闭合区域提取。"""
import hashlib
import json
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageOps
from shapely.geometry import shape,mapping
from shapely.affinity import affine_transform
from gis_geometry import projected_crs,gdal_task

def fit_control_points(points,width,height,tolerance=2):
    if not isinstance(points,list) or not 4<=len(points)<=100:raise ValueError('至少需要4组同名控制点，最多100组')
    array=np.asarray(points,dtype=float)
    if array.shape!=(len(points),4) or not np.isfinite(array).all():raise ValueError('控制点必须为有限数值[u,v,x,y]')
    if np.any(array[:,:2]<0) or np.any(array[:,0]>width) or np.any(array[:,1]>height):raise ValueError('控制点超出原图范围')
    design=np.column_stack((array[:,:2],np.ones(len(array))))
    if np.linalg.matrix_rank(design)<3:raise ValueError('图像控制点不能共线或重复')
    if np.linalg.matrix_rank(array[:,2:]-array[:,2:].mean(axis=0))<2:raise ValueError('地理控制点不能共线')
    coefficients,_,_,_=np.linalg.lstsq(design,array[:,2:],rcond=None)
    residuals=np.linalg.norm(design@coefficients-array[:,2:],axis=1)
    if not 0<float(tolerance)<=100 or residuals.max()>float(tolerance):raise ValueError('配准误差超过阈值；最大误差{:.3f}米，请重新选择控制点'.format(residuals.max()))
    a,b,c=coefficients[:,0];d,e,f=coefficients[:,1]
    if abs(a*e-b*d)<1e-12:raise ValueError('配准变换退化，不能定位图件')
    return dict(matrix=[float(x) for x in (a,b,c,d,e,f)],rmse_m=float(np.sqrt(np.mean(residuals**2))),max_error_m=float(residuals.max()),residuals_m=residuals.tolist())

def red_boundaries(image,matrix):
    with Image.open(image) as original:
        width,height=original.size;rgb=original.convert('RGB');rgb.thumbnail((2048,2048))
        pixels=np.asarray(rgb).astype(float);red=(pixels[:,:,0]>120)&(pixels[:,:,0]>pixels[:,:,1]*1.4)&(pixels[:,:,0]>pixels[:,:,2]*1.4)
        mask=Image.fromarray(red.astype('uint8'),'L');border=ImageOps.expand(mask,1,fill=0);ImageDraw.floodfill(border,(0,0),2)
        enclosed=(np.asarray(border.crop((1,1,mask.width+1,mask.height+1)))==0).astype('uint8')
        if not enclosed.any():return []
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'work') as folder:
            path=Path(folder)/'mask.png';Image.fromarray(enclosed*255).save(path);polygons=gdal_task('mask',path)
        a,b,c,d,e,f=matrix;sx,sy=width/mask.width,height/mask.height;result=[]
        for feature in polygons:
            polygon=shape(feature)
            if polygon.area<16:continue
            transformed=affine_transform(polygon,[a*sx,b*sy,d*sx,e*sy,c,f])
            result.append(dict(type='Feature',properties=dict(method='红色闭合线围合候选，需核对图例、印章及用地范围',area_m2=transformed.area),geometry=mapping(transformed)))
        return sorted(result,key=lambda p:p['properties']['area_m2'],reverse=True)[:100]

def register_image(image,points,crs,out,tolerance=2):
    crs=projected_crs(crs);image=Path(image);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with Image.open(image) as im:
        width,height=im.size
        if width*height>100_000_000:raise ValueError('图像超过一亿像素，请先使用已渲染页面')
        fit=fit_control_points(points,width,height,tolerance);im.save(out/'配准原图.png')
    a,b,c,d,e,f=fit['matrix']
    (out/'配准原图.pgw').write_text('\n'.join(str(v) for v in (a,d,b,e,c+(a+b)/2,f+(d+e)/2)),encoding='ascii')
    (out/'配准原图.prj').write_text(crs.to_wkt(),encoding='utf-8')
    boundaries=red_boundaries(image,fit['matrix'])
    (out/'红线闭合区域候选.geojson').write_text(json.dumps(dict(type='FeatureCollection',crs=dict(type='name',properties=dict(name=crs.to_string())),features=boundaries),ensure_ascii=False),encoding='utf-8')
    return dict(fit,width=width,height=height,points=points,crs=crs.to_wkt(),source_signature=hashlib.sha256(image.read_bytes()).hexdigest(),boundary_candidates=len(boundaries),boundaries=boundaries)
