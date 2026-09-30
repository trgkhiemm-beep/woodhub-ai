# FRONTEND_INTEGRATION — WoodHub AI Agent API v1 (implemented)

> Contract **đã implement** (`CONTRACT_VERSION = "1.0"`). Nguồn chuẩn: `app/api/schemas.py`, OpenAPI sinh tự động `contracts/agent-api.openapi.json` (hoặc `GET /openapi.json` khi server chạy). Frontend chỉ phụ thuộc DTO trong tài liệu này, không phụ thuộc class nội bộ.

## 1. Endpoints

| Method | Path | Ai dùng | Auth |
|---|---|---|---|
| POST | `/v1/agent/chat` | Khách (guest/customer) — chỉ đọc | tùy chọn `Authorization: Bearer <JWT Backend>` |
| POST | `/v1/agent/chat/stream` | như trên, SSE | như trên |
| POST | `/v1/agent/manage/chat` | Admin, supplier — có thay đổi dữ liệu qua xác nhận | bắt buộc; role khác → 403 |
| POST | `/v1/agent/manage/chat/stream` | như trên, SSE | như trên |
| GET | `/v1/agent/actions/{id}` | xem trạng thái thay đổi | chủ action |
| POST | `/v1/agent/actions/{id}/confirm` | nút **Xác nhận** | chủ action (admin/supplier) |
| POST | `/v1/agent/actions/{id}/cancel` | nút **Hủy** | chủ action |
| GET | `/v1/agent/capabilities?profile=customer\|management` | danh sách chức năng người dùng được dùng | tùy chọn |
| GET | `/health` | monitoring | không |
| POST | `/chat` | **DEPRECATED** — định dạng cũ, xem §8 | tùy chọn |

## 2. Auth & role
- Gửi nguyên access token Backend: `Authorization: Bearer <token>`. AI service xác thực qua `GET /api/users/me` của Backend (cache 60 giây).
- Không token → guest. Token sai/hết hạn → **401** (không tự hạ xuống guest).
- **Không gửi role/user_id trong body** — không có field nào như vậy; role chỉ lấy từ token.

## 3. Request
```json
{ "message": "Đổi giá KTV01 thành 8 triệu", "session_id": "s_abc123", "client_message_id": "uuid", "location": {"lat": 10.77, "lng": 106.7} }
```
`message` bắt buộc (≤ `MAX_MESSAGE_CHARS`, mặc định 2000). `session_id` bỏ trống lần đầu và dùng lại giá trị server trả về. `location` chỉ dùng cho tìm xưởng gần.

## 4. Response (`AgentResponse`)
```json
{
  "type": "answer | clarification | confirmation_required | action_result | error",
  "message": "Văn bản tiếng Việt để hiển thị",
  "session_id": "s_abc123",
  "request_id": "7c1e…",
  "blocks": [{"kind": "product_list", "data": [...]}],
  "sources": [{"system": "backend", "resource": "products", "freshness": "realtime", "fetched_at": "…", "record_id": "…", "version": "…"}],
  "action": null,
  "error": null,
  "meta": {"contract_version": "1.0", "profile": "customer", "role": "guest", "planner": "rules", "tools_used": ["get_product"], "data_source": "backend"}
}
```

| `type` | UI |
|---|---|
| `answer` | bong bóng chat + render `blocks` |
| `clarification` | bong bóng chat; có thể render `candidates` |
| `confirmation_required` | bong bóng + **card xác nhận** từ `action` (§5) |
| `action_result` | kết quả thay đổi; `action.state` ∈ completed / unverified / failed / cancelled / expired |
| `error` | thông báo lỗi `error.code`, `error.message` |

`blocks[].kind`: `product_list`, `product_detail`, `product_comparison`, `inventory`, `store_info`, `branch_list`, `workshop_list`, `promotion_list`, `policy`, `knowledge`, `taxonomy`, `design_task`, `candidates`. Tiền là số VND (`2737000.0`) — format phía client.

**Không có dữ liệu đã xác minh**: agent trả `type=answer` với câu "chưa có thông tin đã xác minh…" và `sources=[]`. UI **không** được tự bổ sung thông tin.

## 5. Xác nhận thay đổi (Management Agent)
`type = "confirmation_required"`, `action`:
```json
{
  "id": "a3f…", "tool": "update_product_price", "operation": "SENSITIVE_UPDATE", "state": "pending_confirmation",
  "summary": "Đổi giá Kệ Tivi Gỗ KTV01 (KTV01): 2.737.000 ₫ → 8.000.000 ₫",
  "target": {"type": "product_variant", "id": "…", "label": "Kệ Tivi Gỗ KTV01 (KTV01)"},
  "changes": [{"field": "price", "label": "Giá", "before": 2737000.0, "after": 8000000.0}],
  "warnings": ["Giá thay đổi 192% so với hiện tại — vui lòng kiểm tra kỹ."],
  "confirmation": {"required": true, "level": "strong", "code": "K7P2QX", "expires_at": "…",
                   "confirm_endpoint": "/v1/agent/actions/a3f…/confirm", "cancel_endpoint": "/v1/agent/actions/a3f…/cancel",
                   "chat_phrase": "xác nhận K7P2QX"},
  "verified": null, "error_code": null, "error_message": null, "created_at": "…", "updated_at": "…"
}
```
UI bắt buộc:
1. Card riêng: `summary`, bảng `changes` (before → after), `warnings`, đếm ngược `expires_at`. `level=strong` → màu cảnh báo.
2. **Xác nhận** → `POST confirm_endpoint` body `{"confirmation_code": code, "session_id": …}`; khóa nút khi chờ. Hoặc người dùng gõ `chat_phrase` trong chat.
3. **Hủy** → `POST cancel_endpoint`.
4. Kết quả (`type=action_result`): `completed` + `verified=true` → thành công; `unverified` → cảnh báo "đã gửi nhưng chưa xác minh", **không** hiển thị thành công; `failed` → hiện `error_message`; `expired`/`cancelled` → vô hiệu card.
5. Gọi confirm lặp lại (double click) an toàn: trả cùng kết quả, không thực thi lần 2.
6. `GET /v1/agent/actions/{id}` **không** trả lại `code`.

## 6. Streaming (SSE)
`POST …/chat/stream` → `text/event-stream`:
```text
event: meta     data: {"session_id","request_id","type","meta"}
event: delta    data: {"text": "…"}          (lặp)
event: block    data: {"kind","data"}        (0..n)
event: sources  data: [...]
event: action   data: {ActionOut}            (nếu có)
event: error    data: {"code","message"}     (nếu có)
event: done     data: {"type"}
```

## 7. Lỗi HTTP
Envelope: `{"status":"error","code":"…","message":"…","request_id":"…"}`.

| HTTP | code | UI |
|---|---|---|
| 401 | UNAUTHENTICATED | refresh token hoặc đăng nhập lại |
| 403 | FORBIDDEN | không có quyền (vd customer gọi `/manage`) |
| 404 | ACTION_NOT_FOUND | action không tồn tại/không thuộc người dùng |
| 422 | VALIDATION_ERROR | sửa input |
| 429 | RATE_LIMITED | chờ (mặc định 30 tin/phút/người) |
| 503 | AUTH_UNAVAILABLE | Backend tài khoản không phản hồi |
| 500 | INTERNAL_ERROR | thử lại |

Lỗi trong hội thoại (vd sai mã xác nhận, Backend chậm) trả HTTP 200 với `type="error"` và `error.code` (`CONFIRMATION_MISMATCH`, `ACTION_EXPIRED`, `UPSTREAM_TIMEOUT`, `UPSTREAM_UNAVAILABLE`…).

## 8. `/chat` cũ (deprecated)
Request `{query, session_id, lat?, lng?, image_url?}` → SSE `data: {"type":"chunk","content"}` … `{"type":"debug_data","payload":[{id,name,description,price,status,image_url}]}` hoặc `mixed_data` … `{"type":"done"}`. `image_url` không còn tạo 3D (Backend `/api/custom/ai/generate` đảm nhiệm). Không còn `cart_data`, `3d_generating`.

## 9. Việc của team Frontend
- [ ] Chuyển sang `/v1/agent/chat` (+ `/manage/chat` cho trang quản trị), gửi Bearer token.
- [ ] Render `blocks` theo `kind`; card xác nhận §5; trạng thái `unverified`.
- [ ] Thêm domain production vào `CORS_ORIGINS` của AI service (hiện chỉ `http://localhost:3000`).
- [ ] Cho biết Frontend đang gọi `/chat` trực tiếp hay qua Backend `/api/ai-chat/...`.
