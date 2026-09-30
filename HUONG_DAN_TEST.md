# Hướng dẫn tự kiểm thử WoodHub AI Agent

## 1. Khởi chạy
```bash
.venv/Scripts/uvicorn app.main:app --reload
```
Cần `BACKEND_BASE_URL=https://woodhub-be.onrender.com` trong `.env`. Mở Swagger: http://127.0.0.1:8000/docs

## 2. Test tự động (dữ liệu thật)
```bash
.venv/Scripts/python -m pytest -q
```

## 3. Kịch bản thủ công — `POST /v1/agent/chat` (khách, không cần token)

| Câu hỏi | Kỳ vọng |
|---|---|
| `Tìm kệ tivi dưới 3 triệu` | danh sách sản phẩm thật, giá khớp dữ liệu |
| `Giá Bàn Ăn TB06` | giá từng phiên bản, ghi chú lấy trực tiếp từ hệ thống |
| `co giuong go soi khong` | hiểu tiếng Việt không dấu, trả sản phẩm giường gỗ sồi |
| `So sánh KTV01 và KTV02` | bảng so sánh |
| `Chính sách đổi trả thế nào?` / `Giờ mở cửa?` / `Có voucher không?` | "chưa có thông tin đã xác minh" (Backend chưa có dữ liệu này) |
| `KTV01 còn bao nhiêu cái?` | "chưa có dữ liệu tồn kho đã xác minh" |
| `Thời tiết hôm nay` | từ chối ngoài phạm vi |

## 4. Kịch bản quản trị — `POST /v1/agent/manage/chat` (cần `Authorization: Bearer <token Backend>` của admin/supplier)

| Tin nhắn | Kỳ vọng |
|---|---|
| (supplier) `Đổi giá KTV01 thành 8 triệu` | `confirmation_required`, hiển thị giá cũ → mới + mã xác nhận; **chưa thay đổi gì** |
| `xác nhận <MÃ>` | thực thi → đọc lại → `completed` + `verified=true`, hoặc `unverified`/`failed` kèm lý do |
| `hủy` | hủy thay đổi đang chờ |
| (admin) `Đổi giá KTV01 thành 8 triệu` | từ chối: giá thuộc quyền nhà cung cấp |
| (customer) `/manage/chat` | HTTP 403 |

⚠ Các lệnh ở mục 4 ghi vào dữ liệu thật khi xác nhận. Chỉ dùng tài khoản/dữ liệu thử nghiệm.
