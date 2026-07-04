#app/main.py
"""
Entry point của ứng dụng FastAPI. Chạy bằng:
    uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.config import settings
from app.api.chat import router as chat_router
from app.api.workshops import router as workshop_router
from app.api.products import router as product_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("woodhub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    "lifespan" là cách hiện đại để chạy code lúc startup/shutdown, thay
    thế cho @app.on_event("startup") đã bị deprecate từ FastAPI 0.109+.

    Ở đây mình log lại cấu hình đã tải để bạn xác nhận app khởi động đúng
    môi trường. Lưu ý: nếu Settings() lỗi vì thiếu biến môi trường bắt
    buộc, app sẽ crash TRƯỚC KHI chạy tới đoạn log này - nên nếu bạn thấy
    2 dòng log dưới đây xuất hiện, nghĩa là cấu hình đã hợp lệ 100%.
    """
    logger.info("Đã tải cấu hình cho: %s", settings.PROJECT_NAME)
    logger.info("CORS cho phép các origin: %s", settings.cors_origins_list)
    yield
    logger.info("WoodHub AI Server đang tắt.")


app = FastAPI(title="WOODHUB AI Engine", version="1.0.0", lifespan=lifespan)

# --- CORS ---
# CẢNH BÁO QUAN TRỌNG về cấu hình allow_origins=["*"] trong bản gốc:
#
# Theo chuẩn CORS (Fetch spec), nếu response có header
# "Access-Control-Allow-Credentials: true", thì header
# "Access-Control-Allow-Origin" BẮT BUỘC phải là một origin CỤ THỂ,
# tuyệt đối không được là "*". Lý do: kết hợp "*" với credentials nghĩa
# là BẤT KỲ website nào trên Internet cũng có thể gọi API này kèm theo
# cookie của người dùng đang đăng nhập trên trình duyệt của họ - đây
# chính là dạng lỗ hổng CSRF mà cơ chế CORS sinh ra để ngăn chặn.
#
# FastAPI/Starlette không báo lỗi khi bạn khai báo sai như vậy, nhưng hệ
# quả là: với request có gửi kèm credentials thật, trình duyệt có thể từ
# chối đọc response (lỗi "Failed to fetch" mơ hồ - trớ trêu là đúng lỗi
# cấu hình này định sửa), hoặc nếu "chạy được" thì đó lại là lỗ hổng bảo
# mật thật sự.
#
# Cách đúng: khai báo CHÍNH XÁC danh sách domain được phép, lấy từ biến
# môi trường (settings.CORS_ORIGINS) để đổi giữa dev/staging/production
# mà không cần sửa code.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Bắt lỗi validate input (thiếu field, sai kiểu dữ liệu...). Mặc định
    FastAPI trả {"detail": [...]} khá phức tạp để frontend parse; handler
    này chuẩn hóa lại thành 1 format lỗi đơn giản, nhất quán cho toàn hệ
    thống - frontend chỉ cần viết 1 hàm parse lỗi duy nhất, dùng chung
    cho mọi endpoint (chat, workshops, products).
    """
    first_error = exc.errors()[0]
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "code": "VALIDATION_ERROR",
            "message": first_error.get("msg", "Dữ liệu đầu vào không hợp lệ."),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Bắt MỌI exception không lường trước (bug, lỗi gọi Gemini API, lỗi gọi
    Workshop API...). Không có handler này, lỗi sẽ trả về dạng response
    mặc định của Starlette (không phải JSON thuần), khiến frontend gọi
    response.json() bị crash thêm 1 lần nữa trên chính lỗi gốc.
    Traceback đầy đủ được log ở server để bạn debug, còn người dùng chỉ
    thấy thông báo chung chung, không lộ chi tiết nội bộ (API key, tên
    endpoint...) ra ngoài.
    """
    logger.exception("Lỗi không lường trước tại %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "code": "INTERNAL_ERROR",
            "message": "Đã có lỗi xảy ra ở server, vui lòng thử lại sau.",
        },
    )


# Đăng ký các Router module hóa từ thư mục app/api/.
#
# Gợi ý mở rộng sau này (CHƯA áp dụng ở đây để tránh phá vỡ đường dẫn
# frontend hiện tại đang gọi, vì mình chưa xem nội dung 3 router này):
# nếu muốn version hóa API, có thể thêm prefix chung, ví dụ:
#     app.include_router(chat_router, prefix="/api/v1", tags=["chat"])
# để sau này ra /api/v2 song song mà không ảnh hưởng /api/v1 đang chạy.
app.include_router(chat_router)
app.include_router(workshop_router)
app.include_router(product_router)


@app.get("/", tags=["monitoring"])
def home():
    return {"message": "WoodHub AI Server đang chạy ổn định theo kiến trúc chuẩn Modular!"}


@app.get("/health", tags=["monitoring"])
def health_check():
    """
    Endpoint riêng cho việc kiểm tra "server còn sống" - dùng bởi load
    balancer, uptime checker, hoặc frontend lúc khởi động app để hiển thị
    trạng thái kết nối. Tách riêng khỏi "/" để có ý nghĩa quy ước rõ ràng:
    "/" trả thông tin mô tả app, "/health" chỉ trả trạng thái sống/chết,
    không phụ thuộc logic nghiệp vụ nào khác.
    """
    return {"status": "ok"}