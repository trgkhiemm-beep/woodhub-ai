# app/core/database.py
import os
import socket
from supabase import create_client, Client, ClientOptions
from dotenv import load_dotenv

# --- BẢN VÁ AN TOÀN CHO LỖI IPV6 TRÊN WINDOWS ---
orig_getaddrinfo = socket.getaddrinfo

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # Ép buộc sử dụng IPv4 nếu thư viện httpx để family=0 (unspecified)
    if family == 0:
        family = socket.AF_INET
    return orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = patched_getaddrinfo
# ------------------------------------------------

load_dotenv()

raw_url = os.getenv("SUPABASE_URL")
raw_key = os.getenv("SUPABASE_KEY")

if not raw_url or not raw_key:
    raise ValueError("Thiếu cấu hình SUPABASE_URL hoặc SUPABASE_KEY")

# Tối ưu: Loại bỏ khoảng trắng và dấu gạch chéo ở cuối URL (Nguyên nhân phổ biến gây lỗi)
supabase_url = raw_url.strip().rstrip('/')
supabase_key = raw_key.strip()

# Khởi tạo với cấu hình timeout dài hơn để tránh rớt mạng khi kết nối lần đầu
supabase: Client = create_client(
    supabase_url, 
    supabase_key,
    options=ClientOptions(
        postgrest_client_timeout=30,
        storage_client_timeout=30,
    )
)