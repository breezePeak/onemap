"""批处理入口与工作台使用同一批材料的历史成功缓存。"""
from review_service import BASE,reuse_material_cache


if __name__=='__main__':
    reuse_material_cache(BASE,BASE/'work')
