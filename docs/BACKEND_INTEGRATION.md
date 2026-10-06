# BACKEND_INTEGRATION — WoodHub AI Service ↔ Backend (v2.1)

> Đối chiếu với OpenAPI thật của Backend (`https://woodhub-be.onrender.com/v3/api-docs`), snapshot mới nhất
> `contracts/backend/woodhub-be.openapi.snapshot-2026-10-06.json` (162 operation).
> **v2.0:** AI service là trợ lý khách hàng **CHỈ ĐỌC** — chỉ gọi Backend bằng GET; không còn PUT/PATCH/POST.

---

## 1. Backend → AI service (Backend gọi agent)

| Backend (đã có trong OpenAPI 2026-10-06) | AI service | Trạng thái |
|---|---|---|
| `POST /api/admin/ai-agent/chat` `AdminAiChatRequest{message, sessionId, lat, lng}` | `POST /v1/agent/manage/chat` | ĐÃ CÓ phía Backend; Agent nhận `sessionId` (alias) |
| `POST /api/admin/ai-agent/actions/{actionId}/confirm` `ConfirmActionRequest{confirmationCode, sessionId}` | `POST /v1/agent/actions/{id}/confirm` | Giữ để tương thích; luôn `type=error`, `ACTION_NOT_FOUND` (agent chỉ đọc) |
| `POST /api/admin/ai-agent/actions/{actionId}/cancel` | `POST /v1/agent/actions/{id}/cancel` | như trên |
| `POST /api/ai-chat/sessions/{sessionId}/messages` `{content, lat, lng}` → `AiChatMessageResponse` | `POST /v1/agent/chat` (hoặc `/chat` cũ) | **CHƯA XÁC MINH** Backend đang gọi endpoint nào |

Contract agent: `contracts/agent-api.openapi.json` (request nhận cả `session_id`|`sessionId`, `confirmation_code`|`confirmationCode`,
`client_message_id`|`clientMessageId` — ghi ở `x-aliases`; response snake_case). `AgentChatResponse` của Backend không có `meta`
— không ảnh hưởng chức năng.

| Thành phần | Trách nhiệm |
|---|---|
| **WoodHub Backend** | AUTH + USER ACCESS CONTROL: đăng nhập/đăng ký, phát hành & kiểm tra JWT, role/permission, quyết định ai được gọi chat (khách `/api/ai-chat`, admin `/api/admin/ai-agent`) và ai được xem dữ liệu riêng |
| **AI Agent** | NATURAL LANGUAGE + RETRIEVAL + RECOMMENDATION + ORCHESTRATION — **không** xác thực người dùng, **không** đọc JWT, **không** phân quyền admin/supplier/customer, **không** tự chặn người chưa đăng nhập |
| **Supabase** | SOURCE OF TRUTH (Agent chỉ đọc qua Backend API) |

**Auth (v2.1):** Agent **không** xác thực/phân quyền người dùng — không verify JWT, không gọi `/api/users/me`, không đọc role,
không trả 401/403 vì người dùng. Backend kiểm tra đăng nhập/quyền **trước** khi gọi Agent (vd chỉ admin tới được
`/api/admin/ai-agent/*`). Nếu Backend gửi kèm `Authorization: Bearer <JWT>`, Agent chuyển tiếp nguyên trạng khi gọi lại các API
dữ liệu riêng; Backend tự quyết định. **Không cần đặt `JWT_SECRET` của Backend vào AI service.**

**Server-to-server (khuyến nghị cho production):** đặt `AGENT_SERVICE_API_KEY` (chuỗi ngẫu nhiên dài) ở AI service và cho Backend
gửi header `X-Agent-Api-Key: <giá trị>` mọi request tới Agent; thiếu/sai → 401 `SERVICE_UNAUTHORIZED`. Mặc định tắt để không
phá tích hợp hiện tại. Bổ sung: chỉ cho Backend truy cập Agent ở tầng mạng (private network / allowlist IP).

## 2. AI service → Backend (agent đọc dữ liệu) — allowlist `app/adapters/backend/client.py`, chỉ GET

| Năng lực agent | Endpoint Backend | Trạng thái |
|---|---|---|
| Tìm / gợi ý sản phẩm | `GET /api/products?keyword&categoryId&materialId&minPrice&maxPrice&page&size` | ĐÃ CÓ — Agent có thể dùng |
| Chi tiết, giá, biến thể, nhà cung cấp của sản phẩm | `GET /api/products/{id}` (có `supplierId`, `supplierName`) | ĐÃ CÓ — Agent có thể dùng |
| Danh mục / chất liệu / phòng / phong cách | `GET /api/categories|materials|rooms|styles` | ĐÃ CÓ — Agent có thể dùng |
| Danh sách nhà cung cấp công khai | `GET /api/suppliers/public` | ĐÃ CÓ — Agent có thể dùng |
| Hồ sơ nhà cung cấp (tên, mô tả, email, điện thoại) | `GET /api/suppliers/{id}/public` | ĐÃ CÓ — Agent có thể dùng |
| Chi nhánh của nhà cung cấp (quận/thành phố) | `GET /api/suppliers/{id}/stores` | ĐÃ CÓ — Agent có thể dùng |
| Xưởng gần khách | `GET /api/stores/nearby/workshops?lat&lng&limit` (Backend yêu cầu đăng nhập; Agent chuyển tiếp token) | ĐÃ CÓ — Agent có thể dùng |
| Đơn đặt làm của khách | `GET /api/custom-orders/my`, `/api/custom-orders/{id}` (Backend kiểm tra token chuyển tiếp) | ĐÃ CÓ — Agent có thể dùng |
| Trạng thái task 3D | `GET /api/custom/ai/tasks/{id}` | ĐÃ CÓ — Agent có thể dùng |
| Tồn kho | `GET /api/variants/{id}/inventory` — **chỉ supplier chủ** | Một phần (khách → "chưa có dữ liệu tồn kho") |

## 3. Backend cần làm

**[MUST HAVE]**
- **Không cần sửa code Backend** cho việc bỏ user-auth ở Agent: Backend có thể tiếp tục gửi `Authorization` như hiện nay.
- Xác nhận `/api/ai-chat/sessions/{id}/messages` gọi `POST /v1/agent/chat` và **chuyển tiếp `Authorization` của khách** (để Agent
  lấy được đơn hàng/task 3D/xưởng gần qua Backend; thiếu → Backend từ chối, Agent báo chưa xem được).
- Giữ Backend thức (Render gói free ngủ: lần gọi đầu đo được 80–92 s) — nâng gói hoặc keep-alive.
- Tài khoản test (customer, supplier, admin) để kiểm thử E2E.

**[SHOULD HAVE]**
- Bật `AGENT_SERVICE_API_KEY` + gửi `X-Agent-Api-Key` từ Backend (bảo vệ server-to-server).

**[SHOULD HAVE — dữ liệu]** (Agent đã sẵn sàng; chỉ cần thêm adapter + dòng allowlist khi Backend có API)
- **FAQ / hướng dẫn Web/App** — THIẾU, Backend cần triển khai: `GET /api/knowledge/faqs?category&page`, tìm kiếm
  `POST /api/knowledge/search {query, kinds, topK≤5}` → `{documentId, kind, title, snippet, score, version}`. Supabase hiện không có bảng FAQ.
- **Chính sách của nhà cung cấp** (giao hàng, đổi trả, bảo hành) — THIẾU: thêm trường/endpoint trong hồ sơ NCC, vd
  `GET /api/suppliers/{id}/public` trả `policies{shipping, return, warranty}` (văn bản do NCC nhập).
- **Giờ hoạt động chi nhánh** — THIẾU: `StorePublicResponse.openingHours`.
- **Tình trạng hàng công khai** — THIẾU: `GET /api/variants/{id}/availability` → `{status: in_stock|low_stock|out_of_stock|unknown, asOf}`
  (không cần số lượng). `store_inventory` hiện 0 dòng.
- **Tra theo SKU** — THIẾU: `GET /api/variants/by-sku/{sku}`; bổ sung SKU cho 26/27 biến thể đang `NULL` (hiện tra một mã
  không tồn tại phải quét catalog ~30 request).
- **Khuyến mãi** (chỉ đọc, nếu có): `ProductResponse.variants[].salePrice` hoặc `GET /api/promotions?productId` — hiện không có
  dữ liệu nên agent trả "chưa cập nhật thông tin".

**Không cần nữa (v2.0):** API agent-actions, audit API cho thao tác của agent, endpoint ghi cho agent, platform info chung của
WoodHub (thông tin liên hệ lấy theo nhà cung cấp).

## 4. Endpoint Backend mà AI service không bao giờ gọi
Mọi PUT/PATCH/POST/DELETE (CRUD sản phẩm/biến thể/tồn kho/danh mục/khuyến mãi…), auth (login/register/OTP/refresh), users CRUD,
payments, subscriptions, chat customer↔supplier. `BackendClient` từ chối mọi request không có trong allowlist GET trước khi gửi.

## 5. Kiến trúc triển khai

```text
Android / Web (chỉ giữ JWT của Backend)
 → WoodHub Backend (AUTH + phân quyền, quota ai_chat, lưu lịch sử chat, CRUD, tạo 3D)
   → AI Agent (/v1/agent/*, X-Agent-Api-Key tùy chọn, không user-auth, chỉ đọc)
     → AWS Bedrock (NLU, chỉ khi bộ luật không chắc)
     → Backend API (GET) → Supabase
```
AI service không cần `SUPABASE_*` ở runtime (chỉ dùng cho test đối chiếu). Nên rotate Supabase secret key nếu từng cấp cho AI service.
