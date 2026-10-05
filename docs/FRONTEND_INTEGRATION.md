# FRONTEND_INTEGRATION — WoodHub AI Agent API v1.2

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
- Gửi nguyên access token Backend: `Authorization: Bearer <token>`. AI service verify chữ ký HS256 bằng `BACKEND_JWT_SECRET`
  (= `JWT_SECRET` của Backend) và hạn `exp`; danh tính = claim `sub` (email), role = claim `role`. Chưa cấu hình secret →
  dự phòng xác thực qua `GET /api/users/me`.
- Endpoint quản trị (`/manage/chat*`, `/actions/*`): thiếu/sai/hết hạn token → **401**; role khác admin/supplier → **403**.
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
  "blocks": [{"kind": "recommendation", "data": {"mode": "recommend", "requirements": {...}, "items": [...], "notes": [...]}}],
  "sources": [{"system": "backend", "resource": "products", "freshness": "realtime", "fetched_at": "…", "record_id": "…", "version": "…", "verified": true}],
  "action": null,
  "error": null,
  "meta": {"contract_version": "1.0", "profile": "customer", "role": "guest", "planner": "llm", "tools_used": ["recommend_products"],
           "intents": ["recommend"], "data_source": "backend"}
}
```

| `type` | UI |
|---|---|
| `answer` | bong bóng chat + render `blocks` |
| `clarification` | bong bóng chat; có thể render `candidates` |
| `confirmation_required` | bong bóng + **card xác nhận** từ `action` (§5) |
| `action_result` | kết quả thay đổi; `action.state` ∈ completed / unverified / failed / cancelled / expired |
| `error` | thông báo lỗi `error.code`, `error.message` |

`blocks[].kind`: `recommendation`, `product_detail`, `product_comparison`, `inventory`, `store_info`, `branch_list`, `workshop_list`, `promotion_list`, `policy`, `knowledge`, `taxonomy`, `design_task`, `candidates`. Tiền là số VND (`2737000.0`) — format phía client.

**`message` ngắn, cố định định dạng** (v1.2): mỗi sản phẩm một dòng `- Tên — 1.990.000đ`; hỏi giá → tên + giá; hỏi tồn kho →
`- Tên — còn N | hết hàng | chưa có dữ liệu tồn kho`; không lời dẫn/CTA/giải thích. Chi tiết (lý do, kích thước, ảnh…) nằm trong `blocks`.

Câu trả lời cố định — UI có thể so khớp nguyên văn:

| Trường hợp | `type` | `message` |
|---|---|---|
| Ngoài phạm vi cửa hàng | `answer` | `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ của cửa hàng.` |
| Không có sản phẩm phù hợp (dữ liệu thật) | `answer` | `Hiện hệ thống chưa cập nhật sản phẩm phù hợp.` |
| Không đọc được dữ liệu sản phẩm (Backend lỗi/timeout/dữ liệu hỏng) | `error` | `Hệ thống chưa thể kiểm tra dữ liệu sản phẩm lúc này.` |
| Thông tin chưa có nguồn đã xác minh (chính sách, khuyến mãi…) | `answer` | `Hiện chưa có thông tin đã xác minh về <chủ đề>.` |

UI **không** được tự bổ sung thông tin khi agent nói chưa có dữ liệu.

### 4.1 Block `recommendation` (tư vấn / tìm sản phẩm)
```json
{"kind": "recommendation", "data": {
  "mode": "recommend | search",
  "requirements": {"category": "bàn ăn", "budget_max": 10000000, "seats": 6},
  "matched": 4, "unverifiable": [],
  "items": [{"id": "uuid", "name": "Bàn ăn gỗ sồi Scandi", "price": 5900000.0, "category": "Bàn", "material": "Gỗ Sồi",
             "dimensions": "160 x 90 x 75", "area_cm2": 14400.0, "seats": 6, "seats_estimated": true,
             "colors": ["Tự nhiên (Natural Oak)"], "image_url": "https://…",
             "reasons": ["Đúng loại bàn ăn", "Giá 5.900.000đ trong ngân sách 10.000.000đ", "~6 người (ước tính theo kích thước)"]}]}}
```
- Mọi item đều **đạt toàn bộ** tiêu chí (loại, ngân sách min/max, chất liệu, màu, số chỗ, kích thước) trên dữ liệu thật; không
  có gợi ý "gần đúng" hay vượt ngân sách. Không còn item → không có block, `message` = câu "chưa cập nhật" ở trên.
- `dimensions`/`seats`/`colors` chỉ có khi tiêu chí cần đọc chi tiết sản phẩm (để tiết kiệm truy vấn), còn lại `null`/`[]`.
- v1.2 bỏ `considered`, `notes`, `misses`. UI nên hiển thị số thứ tự 1, 2, 3 để người dùng có thể nói "mẫu 2".

### 4.2 Hội thoại nhiều lượt
- **Luôn gửi lại `session_id`** server trả về: agent nhớ danh sách vừa hiển thị ("mẫu 2"), sản phẩm đang nói tới ("cái này"), nhu cầu đã nêu (loại, ngân sách, số người) và câu hỏi làm rõ đang chờ.
- `type="clarification"`: agent thiếu thông tin quan trọng (vd "Bạn đang hỏi mẫu nào?", "Ngân sách khoảng bao nhiêu?") — hiển thị như câu hỏi, không phải lỗi.
- Session gắn với tài khoản: dùng `session_id` của người khác sẽ nhận session mới (không lộ ngữ cảnh).
- Giá/tồn kho luôn đọc lại từ Backend mỗi lượt; UI không cần tự làm mới.

## 5. Xác nhận thay đổi (Management Agent)
`type = "confirmation_required"`, `action`:
```json
{
  "id": "a3f…", "tool": "update_product_price", "operation": "SENSITIVE_UPDATE", "state": "pending_confirmation",
  "summary": "Đổi giá Kệ Tivi Gỗ KTV01 (KTV01): 2.737.000đ → 8.000.000đ",
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
