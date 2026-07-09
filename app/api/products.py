import os
import logging
from fastapi import APIRouter, HTTPException
from supabase import create_client, Client

logger = logging.getLogger("woodhub.products")

router = APIRouter()

# 1. Đọc cấu hình kết nối từ biến môi trường hệ thống
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# 2. Khởi tạo Supabase Client an toàn
if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("Thiếu cấu hình SUPABASE_URL hoặc SUPABASE_KEY trong biến môi trường!")
    # Khởi tạo None để tránh crash app lúc start, nhưng sẽ báo lỗi khi gọi API
    supabase: Client = None 
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


@router.get("/api/products")
async def get_products():
    """Lấy toàn bộ danh sách sản phẩm thực tế từ bảng 'products' trong Supabase"""
    
    # Kiểm tra cấu hình kết nối kết nối database sớm
    if supabase is None:
        raise HTTPException(
            status_code=500,
            detail="Cấu hình kết nối cơ sở dữ liệu Supabase chưa được thiết lập."
        )
        
    try:
        # 3. Thực hiện câu lệnh SELECT * FROM products tương đương trong Supabase SQL
        # Chúng ta dùng run_in_threadpool ngầm từ thư viện của Supabase hoặc gọi trực tiếp execute()
        response = supabase.table("products").select("*").execute()
        
        # Dữ liệu thật nằm trong thuộc tính .data của đối tượng trả về
        return response.data

    except Exception as e:
        logger.error(f"Lỗi khi truy vấn danh sách sản phẩm từ Supabase: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail="Không thể lấy dữ liệu sản phẩm từ hệ thống lúc này. Vui lòng thử lại sau!"
        )