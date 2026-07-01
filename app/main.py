from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # 1. Thêm import này để sửa lỗi CORS
from app.api.chat import router as chat_router
from app.api.workshops import router as workshop_router
from app.api.products import router as product_router

app = FastAPI(title="WOODHUB AI Engine", version="1.0.0")

# 2. Cấu hình CORS (Giải quyết triệt để lỗi "Failed to fetch" trên Swagger)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cho phép tất cả các nguồn gửi request tới (Frontend, Swagger Local)
    allow_credentials=True,
    allow_methods=["*"],  # Cho phép tất cả các phương thức GET, POST, PUT, DELETE...
    allow_headers=["*"],  # Cho phép tất cả các Headers đi kèm
)

# Đăng ký các Router module hóa từ thư mục app/api/
app.include_router(chat_router)
app.include_router(workshop_router)
app.include_router(product_router)

@app.get("/")
def home():
    return {"message": "WoodHub AI Server đang chạy ổn định theo kiến trúc chuẩn Modular!"}