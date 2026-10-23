"""校验图片缓存并原子生成OCR副本；坏原件不能变成有效队列项。"""
from pathlib import Path
from PIL import Image

IMAGE_ERRORS=(OSError,ValueError,SyntaxError,EOFError,Image.DecompressionBombError)

def valid_image(path):
    path=Path(path)
    try:
        if not path.is_file() or path.stat().st_size==0:return False
        with Image.open(path) as image:image.load()
        return True
    except IMAGE_ERRORS:return False

def prepare_image(source,destination):
    destination=Path(destination);temporary=destination.with_name(destination.name+'.writing')
    try:
        if hasattr(source,'read'):
            position=source.tell()
            if not source.read(1):raise ValueError('图片为空文件（0字节），无法识别')
            source.seek(position)
        elif Path(source).stat().st_size==0:raise ValueError('图片为空文件（0字节），无法识别')
        with Image.open(source) as original:
            with original.convert('RGB') as image:
                image.thumbnail((2500,2500))
                image.save(temporary,format={'.jpg':'JPEG','.jpeg':'JPEG','.png':'PNG','.bmp':'BMP'}[destination.suffix.lower()])
        temporary.replace(destination)
    finally:temporary.unlink(missing_ok=True)
    text=destination.with_suffix('.txt')
    for path in (text,Path(str(text)+'.layout.json'),Path(str(text)+'.error')):path.unlink(missing_ok=True)
