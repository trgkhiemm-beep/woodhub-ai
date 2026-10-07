# FRONTEND_INTEGRATION — WoodHub AI Agent API v2.2

> Contract **đã implement** (`CONTRACT_VERSION = "1.0"`, tương thích ngược). Nguồn chuẩn: `app/api/schemas.py`, OpenAPI
> `contracts/agent-api.openapi.json` (hoặc `GET /openapi.json`; test `tests/test_contract.py` giữ file khớp code).
> Frontend/App nên gọi **qua Backend** (`/api/ai-chat/...`, `/api/admin/ai-agent/...`); Backend chuyển tiếp sang các endpoint dưới đây.

**v2.0:** agent là trợ lý **khách hàng, CHỈ ĐỌC** — không còn tạo/xác nhận thay đổi dữ liệu. Route, auth và schema giữ nguyên;
`type` `confirmation_required`/`action_result` và field `action` vẫn có trong schema nhưng **không còn được trả** (`action` luôn `null`).

## 1. Endpoints

| Method | Path | Ai dùng | Auth |
|---|---|---|---|
| POST | `/v1/agent/chat` | Backend (`/api/ai-chat/...`) | không cần; `Authorization` nếu có được chuyển tiếp nguyên trạng cho Backend |
| POST | `/v1/agent/chat/stream` | như trên, SSE | như trên |
| POST | `/v1/agent/manage/chat` | Backend (`/api/admin/ai-agent/chat`) — **cùng trợ lý chỉ đọc** | như trên (Backend kiểm tra admin) |
| POST | `/v1/agent/manage/chat/stream` | như trên, SSE | như trên |
| GET | `/v1/agent/actions/{id}` | giữ cho tương thích | không cần; luôn **404** `ACTION_NOT_FOUND` |
| POST | `/v1/agent/actions/{id}/confirm` | giữ cho tương thích (Backend `/api/admin/ai-agent/actions/{id}/confirm`) | không cần; luôn **200** `type=error`, `error.code=ACTION_NOT_FOUND` |
| POST | `/v1/agent/actions/{id}/cancel` | giữ cho tương thích | như trên |
| GET | `/v1/agent/capabilities?profile=customer\|management` | danh sách chức năng người dùng được dùng | tùy chọn |
| GET | `/health` | monitoring | không |
| POST | `/chat` | **DEPRECATED** — định dạng cũ, xem §8 | tùy chọn |

## 2. Auth — do Backend đảm nhiệm
- **Agent không xác thực và không phân quyền người dùng.** Đăng nhập, JWT, role, ai được chat/xem gì: Backend quyết định
  trước khi chuyển request sang Agent. Agent không bao giờ trả 401/403 vì người dùng chưa đăng nhập/không đủ quyền.
- Backend có thể gửi kèm `Authorization: Bearer <JWT người dùng>`: Agent **không giải mã/không kiểm tra**, chỉ chuyển tiếp
  nguyên trạng khi gọi lại Backend cho dữ liệu riêng (đơn hàng, task 3D, xưởng gần). Backend từ chối → `message` =
  `Hệ thống WoodHub chưa cho phép xem thông tin này. Nếu đây là thông tin tài khoản của bạn, vui lòng đăng nhập trên WoodHub rồi thử lại.`
- Server-to-server (tùy chọn): nếu AI service cấu hình `AGENT_SERVICE_API_KEY`, Backend phải gửi header `X-Agent-Api-Key`;
  sai/thiếu → **401** `SERVICE_UNAUTHORIZED`. Web/App **không bao giờ** giữ khóa này.
- `meta.role` luôn `"customer"` (giữ field cho tương thích).

## 3. Request

```json
{ "message": "bàn học dưới 2tr5", "session_id": "s_abc123", "client_message_id": "uuid", "location": {"lat": 10.77, "lng": 106.7} }
```

| Field | Bắt buộc | Alias camelCase (Backend Spring) | Ghi chú |
|---|---|---|---|
| `message` | có | `content` (Backend `SendAiMessageRequest`), `query` | ≤ `MAX_MESSAGE_CHARS` (mặc định 2000) |
| `session_id` | không | `sessionId` | bỏ trống lần đầu; gửi lại giá trị server trả về |
| `client_message_id` | không | `clientMessageId` | chống gửi trùng |
| `lat`, `lng` | không | `latitude`, `longitude`/`lon`; hoặc `location{lat,lng}` | GPS thiết bị, chỉ dùng tìm xưởng gần. Thiếu một trong hai, không phải số, ngoài [-90,90]/[-180,180], (-90,-180), (0,0) → bỏ qua (không lỗi 422) |

Confirm (`POST /v1/agent/actions/{id}/confirm`): `{"confirmation_code": "…", "session_id": "…"}` — nhận cả
`confirmationCode`, `sessionId`. Alias được ghi trong OpenAPI ở `x-aliases`. **Response luôn snake_case.**

## 4. Response (`AgentResponse`)
```json
{
  "type": "answer | clarification | error",
  "message": "- Bàn Học Sinh Gỗ Thông Tự Nhiên Có Gắn Kệ Sách — 1.750.000đ",
  "session_id": "s_abc123",
  "request_id": "7c1e…",
  "blocks": [{"kind": "recommendation", "data": {"mode": "recommend", "requirements": {...}, "items": [...]}}],
  "sources": [{"system": "backend", "resource": "products", "freshness": "realtime", "fetched_at": "…", "record_id": "…", "version": null, "verified": true}],
  "action": null,
  "error": null,
  "meta": {"contract_version": "1.0", "profile": "customer", "role": "customer", "planner": "rules", "tools_used": ["recommend_products"],
           "intents": ["recommend"], "data_source": "backend"}
}
```

| `type` | UI |
|---|---|
| `answer` | bong bóng chat + render `blocks` |
| `clarification` | câu hỏi lại (không phải lỗi); có thể render block `candidates` |
| `error` | `error.code`, `error.message` (vd Backend dữ liệu lỗi, `ACTION_NOT_FOUND`) |

`blocks[].kind` đang được trả: `recommendation`, `product_detail`, `product_comparison`, `inventory`, `supplier_info`,
`order_status`, `branch_list`, `workshop_list`, `knowledge`, `taxonomy`, `design_task`, `candidates`
(`store_info`, `promotion_list`, `policy` còn trong enum để tương thích, không còn được trả). Tiền là số VND — format phía client.

**`message` ngắn**: mỗi sản phẩm một dòng `- Tên — 1.990.000đ` (tìm kiếm: tối đa 5, giá tăng dần; tư vấn: tối đa 3; hoặc đúng
số khách nêu, ≤5); khách hỏi "còn hàng" → thêm `— còn hàng | sắp hết | tồn kho: chưa có thông tin`; hỏi tồn kho một sản phẩm →
`- Tên — còn hàng | sắp hết | hết hàng | chưa có dữ liệu tồn kho`; so sánh → bảng markdown (Giá, Kích thước, Chất liệu, Tồn kho,
Nhà cung cấp; ô thiếu = "Chưa có thông tin"). Không lời dẫn/CTA. Chi tiết nằm trong `blocks`.

Câu trả lời cố định — UI có thể so khớp nguyên văn:

| Trường hợp | `type` | `message` |
|---|---|---|
| Ngoài phạm vi WoodHub | `answer` | `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ trên WoodHub.` |
| Không có dữ liệu phù hợp (dữ liệu thật) | `answer` | `Hiện hệ thống chưa cập nhật thông tin phù hợp.` |
| Backend lỗi/timeout/dữ liệu hỏng | `error` | `Hiện hệ thống chưa thể kiểm tra thông tin này.` |
| Backend giới hạn tần suất (429) | `error` (`error.code=UPSTREAM_RATE_LIMITED`) | `Hệ thống WoodHub đang bận, vui lòng thử lại sau ít phút.` |
| Yêu cầu sửa/xóa/tạo dữ liệu | `answer` | `Trợ lý AI chỉ hỗ trợ tra cứu và tư vấn, không thay đổi dữ liệu. Vui lòng cập nhật qua trang quản trị của WoodHub.` |
| Chưa có nguồn đã xác minh (chính sách, giờ hoạt động, FAQ) | `answer` | `Hiện chưa có thông tin đã xác minh về …` |

UI **không** được tự bổ sung thông tin khi agent nói chưa có dữ liệu.

### 4.1 Block `recommendation`
```json
{"kind": "recommendation", "data": {
  "mode": "recommend | search", "distinct_suppliers": false,
  "requirements": {"category": "bàn học", "budget_max": 2500000},
  "matched": 1, "unverifiable": [],
  "items": [{"id": "uuid", "name": "Bàn Học Sinh Gỗ Thông Tự Nhiên Có Gắn Kệ Sách", "price": 1750000.0, "category": "Bàn học",
             "supplier": "<tên nhà cung cấp>", "material": "Gỗ Thông", "dimensions": null, "area_cm2": null, "seats": null,
             "seats_estimated": false, "colors": [], "image_url": "https://…", "reasons": ["Đúng loại bàn học", "…"]}]}}
```
- Mọi item **đạt toàn bộ** tiêu chí trên dữ liệu thật; không gợi ý gần đúng/vượt ngân sách. Không còn item → câu "chưa cập nhật".
- `distinct_suppliers=true` (khách hỏi "từ các nhà cung cấp khác nhau"): mỗi nhà cung cấp tối đa 1 mẫu; `message` kèm tên NCC.
- UI nên hiển thị số thứ tự 1, 2, 3 để khách nói "mẫu 2".

### 4.2 Block `supplier_info` (schema `SupplierInfoBlockData`)
```json
{"kind": "supplier_info", "data": {
  "id": "uuid", "name": "<tên nhà cung cấp>", "type": "retailer", "description": "…",
  "phone": "<điện thoại công khai>", "email": "<email công khai>",
  "stores": [{"id": "uuid", "name": "<tên>", "district": "…", "city": "…", "kind": "retailer", "supplier_id": "uuid"}],
  "product": "Kệ Tivi Gỗ KTV01", "fields": ["address"], "topic": null}}
```
- Nhà cung cấp xác định theo tên, mã sản phẩm hoặc sản phẩm đang nói tới ("Shop này ở đâu?"). Không xác định được →
  `type=clarification` + block `candidates` = danh sách tên nhà cung cấp thật.
- Backend chỉ công khai quận/thành phố của chi nhánh; **không có giờ hoạt động** → agent nói "chưa có thông tin đã xác minh".
- `topic` (shipping/return/warranty/payment/…): khách hỏi chính sách của nhà cung cấp; Backend chưa có dữ liệu chính sách →
  agent nói chưa có và đưa liên hệ thật của nhà cung cấp.

### 4.3 Block `order_status` (schema `OrderStatusBlockData`, data là danh sách)
```json
{"kind": "order_status", "data": [{"id": "uuid", "order_number": "…", "status": "<giá trị nguyên văn từ Backend>",
  "workshop_name": "…", "total_amount": 12000000.0, "lead_time_days": 20, "created_at": "…", "updated_at": "…",
  "history": [{"from_status": "…", "to_status": "…", "note": "…", "created_at": "…"}]}]}
```
Đơn đặt làm (custom order) do Backend trả theo token Backend chuyển tiếp (Backend quyết định quyền xem). Agent không hứa thời gian/phí giao hàng.

### 4.4 Hội thoại nhiều lượt
- **Luôn gửi lại `session_id`** (hoặc `sessionId`): agent nhớ danh sách vừa hiển thị ("mẫu 2"), sản phẩm đang nói tới
  ("cái này", "shop này"), nhu cầu đã nêu và câu hỏi làm rõ đang chờ.
- Session gắn với tài khoản: dùng `session_id` của người khác sẽ nhận session mới.
- Giá/tồn kho/đơn hàng luôn đọc lại từ Backend mỗi lượt.

## 5. Không còn xác nhận thay đổi
Agent chỉ đọc. Không còn card xác nhận: mọi yêu cầu thay đổi dữ liệu nhận câu trả lời "chỉ hỗ trợ tra cứu". Các endpoint
`/actions/*` vẫn tồn tại để Backend không lỗi: confirm/cancel → `type=error`, `error.code=ACTION_NOT_FOUND`; GET → 404.

## 6. Streaming (SSE)
`POST …/chat/stream` → `text/event-stream` (câu trả lời được tính xong rồi chia nhỏ; mobile nên dùng endpoint JSON):
```text
event: meta     data: {"session_id","request_id","type","meta"}
event: delta    data: {"text": "…"}          (lặp)
event: block    data: {"kind","data"}        (0..n)
event: sources  data: [...]
event: error    data: {"code","message"}     (nếu có)
event: done     data: {"type"}
```

## 7. Lỗi HTTP
Envelope: `{"status":"error","code":"…","message":"…","request_id":"…"}`.

| HTTP | code | UI |
|---|---|---|
| 401 | SERVICE_UNAUTHORIZED | chỉ khi bật `AGENT_SERVICE_API_KEY` và Backend gửi thiếu/sai `X-Agent-Api-Key` (lỗi cấu hình server) |
| 404 | ACTION_NOT_FOUND | `GET /actions/{id}` (không còn action) |
| 422 | VALIDATION_ERROR | sửa input |
| 429 | RATE_LIMITED | rate limit của **Agent** (header `X-RateLimit-Layer: ai-agent`, `Retry-After`); mặc định 600/phút theo IP |
| 500 | INTERNAL_ERROR | thử lại |

Lỗi trong hội thoại (Backend chậm/dữ liệu hỏng) trả HTTP 200 với `type="error"` và `error.code` (`UPSTREAM_TIMEOUT`,
`UPSTREAM_UNAVAILABLE`, `MALFORMED_RESPONSE`, `ACTION_NOT_FOUND`…).

## 8. `/chat` cũ (deprecated)
Request `{query|content|message, session_id?|sessionId?, lat?, lng?, image_url?}` → SSE `data: {"type":"chunk","content"}` … `{"type":"debug_data","payload":[{id,name,description,price,status,image_url}]}` hoặc `mixed_data` … `{"type":"done"}`. `image_url` không còn tạo 3D (Backend `/api/custom/ai/generate` đảm nhiệm).

## 9. Việc của team Frontend / App
- [ ] Gọi agent **qua Backend**; không đặt bất kỳ key/secret nào trong web/APK.
- [ ] Render `blocks` theo `kind` (thêm `supplier_info`, `order_status`); bỏ card xác nhận thay đổi.
- [ ] Timeout ~30 s và trạng thái "đang kết nối" (Backend Render gói free ngủ khi rảnh).
- [ ] Thêm domain production vào `CORS_ORIGINS` của AI service nếu web gọi trực tiếp.
