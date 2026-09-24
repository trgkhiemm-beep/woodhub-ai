# 📖 HƯỚNG DẪN TỰ KIỂM THỬ HỆ THỐNG WOODHUB CHATBOT (AWS BEDROCK)

Tài liệu này hướng dẫn chi tiết từng bước để bạn có thể tự kiểm thử (test) hệ thống WoodHub AI Chatbot sau khi chuyển đổi sang **AWS Bedrock (Claude 3 Haiku)**.

---

## 🛠️ BƯỚC 1: CẤU HÌNH AWS BEDROCK API KEY

Mở file [.env](file:///c:/Users/ADMIN/OneDrive/Ta%CC%80i%20li%C3%AA%CC%A3u/FPT%20CODE/Project%20WoodHub/woodhub-ai/.env) và cập nhật thông tin tài khoản AWS của bạn:

```env
# --- AWS BEDROCK CONFIG ---
AWS_ACCESS_KEY_ID=đọc_key_từ_tài_khoản_aws_của_bạn
AWS_SECRET_ACCESS_KEY=đọc_secret_từ_tài_khoản_aws_của_bạn
AWS_DEFAULT_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
```

> 💡 *Ghi chú:* Nếu chưa có AWS Key, hệ thống vẫn tự động chạy ở chế độ **Fallback an toàn** mà không bị văng lỗi crash server.

---

## 🚀 BƯỚC 2: KHỞI CHẠY SERVER

Mở Terminal trong thư mục dự án và chạy lệnh khởi động server:

```bash
uvicorn app.main:app --reload
```

Khi màn hình xuất hiện thông báo:
```text
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```
Nghĩa là server FastAPI đã sẵn sàng nhận kết nối!

---

## 🧪 BƯỚC 3: CÁC CÁCH TỰ TEST HỆ THỐNG

### Cách 1: Test tự động bằng Script (`test_bedrock.py`)

Trong thư mục dự án đã có sẵn file script unit test [test_bedrock.py](file:///c:/Users/ADMIN/OneDrive/Ta%CC%80i%20li%C3%AA%CC%A3u/FPT%20CODE/Project%20WoodHub/woodhub-ai/test_bedrock.py).

Chạy lệnh:
```bash
python test_bedrock.py
```

**Kết quả mong đợi:**
Màn hình sẽ in ra thông báo xanh 4 bài test vượt qua:
- `TEST 1: Bedrock Service Intent Extraction` -> Bóc tách từ khóa và khoảng giá chuẩn JSON.
- `TEST 2: Bedrock Service Response Streaming` -> Stream đúng từng từ (chunk).
- `TEST 3 & 4: Fallback handling` -> Xử lý lỗi mượt mà khi mất mạng hoặc không có key.

---

### Cách 2: Test giao diện Swagger UI (Trực quan trên Trình duyệt)

1. Mở trình duyệt web bất kỳ (Chrome / Edge / Firefox).
2. Truy cập đường dẫn: **`http://127.0.0.1:8000/docs`**
3. Tìm đến endpoint **`POST /chat`**.
4. Bấm nút **Try it out**.
5. Nhập JSON request mẫu:
   ```json
   {
     "query": "Tìm cho mình bàn làm việc gỗ sồi dưới 5 triệu",
     "session_id": "user_test_01"
   }
   ```
6. Bấm **Execute** và xem phản hồi Server-Sent Events (SSE).

---

### Cách 3: Test bằng cURL trong Terminal / Command Prompt

Mở một cửa sổ Terminal mới và dán một trong các câu lệnh test bên dưới:

#### 1. Test Tìm kiếm sản phẩm (AI bóc tách ý định & giá):
```bash
curl -X POST "http://127.0.0.1:8000/chat" ^
     -H "Content-Type: application/json" ^
     -d "{\"query\": \"Tìm bàn làm việc gỗ sồi dưới 5 triệu\", \"session_id\": \"sess_1\"}"
```

#### 2. Test Câu chào hỏi (Lớp đánh chặn nhanh):
```bash
curl -X POST "http://127.0.0.1:8000/chat" ^
     -H "Content-Type: application/json" ^
     -d "{\"query\": \"Xin chào shop\", \"session_id\": \"sess_2\"}"
```

#### 3. Test Câu hỏi ngoài phạm vi (Out of Scope):
```bash
curl -X POST "http://127.0.0.1:8000/chat" ^
     -H "Content-Type: application/json" ^
     -d "{\"query\": \"Dự báo thời tiết Hà Nội hôm nay thế nào?\", \"session_id\": \"sess_3\"}"
```

#### 4. Test Báo giá Custom 3D Parametric (Nhập 3 kích thước Dài x Rộng x Cao):
```bash
curl -X POST "http://127.0.0.1:8000/chat" ^
     -H "Content-Type: application/json" ^
     -d "{\"query\": \"Báo giá bàn học 120x60x75cm bằng gỗ sồi\", \"session_id\": \"sess_4\"}"
```

#### 5. Test Gợi ý Xưởng/Showroom gần nhất (Truyền tọa độ GPS `lat`/`lng`):
```bash
curl -X POST "http://127.0.0.1:8000/chat" ^
     -H "Content-Type: application/json" ^
     -d "{\"query\": \"Tìm xưởng làm bàn gỗ sồi gần đây\", \"lat\": 21.0285, \"lng\": 105.8542, \"session_id\": \"sess_5\"}"
```

---

## 📋 BẢNG KỊCH BẢN KIỂM THỬ (CHECKLIST)

| STT | Kịch bản Test | Dữ liệu Test | Phản hồi mong đợi từ Chatbot |
|-----|---------------|--------------|-------------------------------|
| 1 | Tìm kiếm sản phẩm | `"Cho mình tìm ghế ăn gỗ tần bì"` | Trả về stream tư vấn ngắn gọn & danh sách sản phẩm từ DB |
| 2 | Lọc theo giá | `"Bàn trà gỗ óc chó từ 2 đến 4 triệu"` | AI bóc tách `price_min: 2000000`, `price_max: 4000000` |
| 3 | Chào hỏi | `"Chào bạn"` | Phản hồi nhanh câu chào thương hiệu WoodHub |
| 4 | Chặn ngoài phạm vi | `"Viết code Python giúp mình"` | Phản hồi từ chối khéo léo (chỉ tư vấn nội thất WoodHub) |
| 5 | Custom 3D | `"Bàn 100x50x30cm gỗ sồi"` | Tính giá gia công 3D theo kích thước |
