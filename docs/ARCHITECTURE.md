# WoodHub AI Agent — Architecture (implemented, v1.0)

> Trạng thái: **đã implement** trên nhánh `feature/ai-agent` (2026-09-30). Bản đề xuất Phase 1 được thay bằng bản này.
> Lịch sử thay đổi: `docs/CHANGELOG.md`. Contract: `docs/FRONTEND_INTEGRATION.md`, `docs/BACKEND_INTEGRATION_SPEC.md`.

## 1. Quyết định kiến trúc

| ID | Quyết định | Ghi chú |
|---|---|---|
| DEC-1 | Hai agent trên một core: **Customer Agent** (`/v1/agent/chat`) và **Management Agent** (`/v1/agent/manage/chat`) | chung orchestrator, khác `AgentProfile` và tập tool |
| DEC-2 (sửa) | Role: `guest`, `customer`, `supplier`, `admin` (theo Backend). Supplier dùng Management Agent **chỉ với tool trên dữ liệu của chính họ** | sửa từ Phase 1 để tôn trọng RBAC thật của Backend (supplier là chủ sản phẩm/giá/tồn kho) |
| DEC-4 | Admin **không** sửa giá/tồn kho/mô tả sản phẩm của supplier | Backend chỉ cho supplier chủ; agent từ chối admin kèm giải thích |
| DEC-5 | **Backend là source of truth duy nhất**; AI service không truy cập DB | bỏ Supabase secret key khỏi runtime |
| DEC-6 | LLM qua **Bedrock Converse API**, tùy chọn (`AGENT_PLANNER=llm`); mặc định planner deterministic | model phải hỗ trợ tool use |
| DEC-9 | Mọi mutation cần xác nhận gắn **action_id + mã**; không bao giờ thực thi khi chưa xác nhận | |
| DEC-11 | **Không có dữ liệu mock** trong runtime lẫn test; test đọc dữ liệu thật và chặn ghi | yêu cầu của chủ dự án |
| DEC-12 | Năng lực Backend chưa có ⇒ adapter ném `CapabilityUnavailable` ⇒ agent trả "chưa có thông tin đã xác minh" | không bịa, không fallback sang dữ liệu khác |

## 2. Tổng quan

```text
Frontend / Backend proxy ──HTTP (contract agent-api v1)──┐
                                                         ▼
 app/api           agent.py (/v1/agent/*, SSE) · legacy.py (/chat, deprecated) · deps.py (auth, rate limit)
                      │  Principal = GET /api/users/me (JWT của Backend) — không bao giờ từ nội dung tin nhắn
                      ▼
 app/agent         orchestrator.AgentService
                      ├─ planner.RulePlanner  (deterministic; luôn xử lý xác nhận/hủy)
                      ├─ llm.LLMPlanner       (Bedrock Converse tool use, tùy chọn, fallback về rules)
                      ├─ executor.ToolExecutor ── permission → validate → timeout → READ | PROPOSE
                      ├─ actions.ActionService ── confirm → check_fresh → execute → verify → audit
                      ├─ composer             (trả lời CHỈ từ ToolResult + grounding guard)
                      └─ session.SessionStore (TTL, giới hạn, gắn chủ sở hữu)
                      ▼
 app/tools         read_tools (12) · mutation_tools (9) · registry (quyền, bất biến an toàn)
                      ▼
 app/ports.py      Identity · Catalog · Inventory · Store · Promotion · Knowledge · Design   (Protocol)
                      ▼
 app/adapters/backend   client.py (allowlist 20 endpoint, timeout, retry GET/PUT, map lỗi) · adapters.py
                      ▼
 WoodHub Backend (Spring Boot, https://woodhub-be.onrender.com) ──► Supabase Postgres
```

Agent core (`app/agent`, `app/tools`) chỉ import `app/ports.py` và `app/domain/*`. Đổi Backend = viết adapter mới; `app/container.py` là nơi duy nhất nối implementation.

## 3. Luồng theo loại nhiệm vụ

| Loại | Ví dụ (dữ liệu thật) | Tool | Luồng |
|---|---|---|---|
| READ | "Giờ mở cửa là mấy giờ?" | `get_store_info` | Backend chưa có API → *"chưa có thông tin đã xác minh"* |
| SEARCH | "Tìm kệ tivi dưới 3 triệu" | `search_products` | `/api/products?keyword=kệ tivi&maxPrice=3000000` (tự khôi phục dấu; nới lỏng có ghi chú khi 0 kết quả) |
| REALTIME | "Giá Bàn Ăn TB06" / "KTV01 còn bao nhiêu?" | `get_product` / `get_inventory` | đọc trực tiếp trong lượt; tồn kho chỉ supplier chủ đọc được → khách nhận *chưa xác minh* |
| UPDATE | "Cập nhật mô tả KTV01: …" | `update_product_description` | propose (before = mô tả thật) → xác nhận standard → PUT → verify |
| SENSITIVE UPDATE | "Đổi giá KTV01 thành 8 triệu" | `update_product_price` | propose (2.737.000 → 8.000.000, cảnh báo biên độ) → xác nhận strong → PUT → verify → audit |
| ACTION | "Tạo campaign giảm 20% cho bàn" | `create_promotion` | phân giải danh mục thật, đếm sản phẩm ảnh hưởng → xác nhận strong → Backend chưa có API → FAILED rõ ràng |

## 4. State machine của mutation

```text
PENDING_CONFIRMATION ──(mã đúng, còn hạn, còn quyền)──► CONFIRMED ──► EXECUTING ──► VERIFIED ──► COMPLETED
        │                                                   │             ├──► UNVERIFIED (verify lệch / timeout / dữ liệu hỏng)
        ├──► CANCELLED (người dùng hủy / sai mã 5 lần / mất quyền)   └──► FAILED (dữ liệu cũ STALE_DATA, lỗi Backend)
        └──► EXPIRED (TTL 10 phút standard, 5 phút strong)
```

- Xác nhận: chat `xác nhận <MÃ>` hoặc `POST /v1/agent/actions/{id}/confirm {confirmation_code}`. "ok", "đồng ý" không kèm mã **không** thực thi.
- Chỉ người tạo action xác nhận được; action của người khác trả 404 (không lộ sự tồn tại).
- Xác nhận lặp lại trả kết quả cũ, không ghi lần 2 (lock + state machine). `create_promotion` gửi `idempotency_key = action_id`.
- `check_fresh` đọc lại trước khi ghi: khác snapshot ⇒ FAILED `STALE_DATA`.
- PATCH tồn kho (delta, không idempotent) không bao giờ retry. Timeout khi ghi ⇒ UNVERIFIED, không báo thành công.

## 5. Source of truth

| Dữ liệu | Nguồn | Độ tươi | Ghi chú |
|---|---|---|---|
| Sản phẩm, giá, biến thể, ảnh, danh mục, vật liệu, phòng, phong cách | Backend `/api/products…`, `/api/categories`… | realtime mỗi lượt | không cache |
| Tồn kho | Backend `/api/variants/{id}/inventory` (supplier chủ) | realtime | công khai: GAP B.3 |
| Chi nhánh (cửa hàng của nhà bán lẻ) | `/api/suppliers/public` + `/api/suppliers/{id}/stores` | reference | Backend chỉ công khai quận/thành phố |
| Xưởng gần khách | `/api/stores/nearby/workshops` | reference | cần đăng nhập |
| Task 3D | `/api/custom/ai/tasks/{id}` | realtime | chỉ chủ task |
| Thông tin cửa hàng, khuyến mãi, chính sách, FAQ, hướng dẫn | **chưa có** (GAP B.1/B.6/B.7) | — | trả UNKNOWN |

Ngữ cảnh hội thoại chỉ lưu `last_product_id`; mọi con số được đọc lại từ Backend mỗi lượt. Không có vector store/RAG trong runtime cho tới khi Backend cung cấp knowledge API (retriever cục bộ đã bị gỡ vì không có nguồn dữ liệu thật).

## 6. Tools

| Tool | Loại | Role | Xác nhận | Endpoint Backend |
|---|---|---|---|---|
| get_store_info | READ | all | — | GAP B.1 |
| list_branches | READ | all | — | `/api/suppliers/public`, `/api/suppliers/{id}/stores` |
| find_nearby_workshops | READ | đã đăng nhập | — | `/api/stores/nearby/workshops` |
| search_products | SEARCH | all | — | `/api/products` |
| get_product | REALTIME | all | — | `/api/products/{id}` |
| compare_products | READ | all | — | `/api/products/{id}` |
| get_inventory | REALTIME | all (thực tế: supplier chủ) | — | `/api/variants/{id}/inventory` |
| get_promotions | REALTIME | all | — | GAP B.6 |
| get_policy | READ | all | — | GAP B.7 |
| search_knowledge | SEARCH | all | — | GAP B.7 |
| list_taxonomy | READ | all | — | `/api/categories`, `/materials`, `/rooms`, `/styles` |
| get_design_task_status | REALTIME | đã đăng nhập | — | `/api/custom/ai/tasks/{id}` |
| update_product_description | UPDATE | supplier | standard | `PUT /api/products/{id}` |
| update_product_price | SENSITIVE | supplier | strong | `PUT /api/variants/{id}` |
| adjust_inventory | SENSITIVE | supplier | strong | `PATCH /api/stores/{sid}/inventory/{vid}` |
| update_store_info | SENSITIVE | admin | strong | GAP B.1 |
| upsert_faq | UPDATE | admin | standard | GAP B.7 |
| create_promotion | ACTION | admin | strong | GAP B.6 |
| set_promotion_status | SENSITIVE | admin | strong | GAP B.6 |
| upsert_category | UPDATE | admin | standard | `POST/PUT /api/categories` |
| upsert_material | UPDATE | admin | standard | `POST/PUT /api/materials` |

Không tồn tại (kiểm tra khi khởi động): `execute_sql`, `update_anything`, `run_arbitrary_command`, `http_request`, `confirm_action`, mọi `delete_*`.

Mã sản phẩm: dữ liệu thật có 26/27 biến thể **chưa có SKU**; mã model nằm trong tên (vd `KTV01` trong "Kệ Tivi Gỗ KTV01"). Tool nhận "mã" = SKU hoặc mã model; nếu sản phẩm có nhiều biến thể mà không có SKU, agent hỏi lại.

## 7. Permission (2 lớp)

1. **Agent** (`ToolRegistry.check`): profile × role × `allowed_roles` × `requires_auth`; kiểm tra **trước** validate input; LLM chỉ nhận schema của tool được phép; từ chối được audit (`permission.denied`).
2. **Backend**: mọi call dùng JWT của chính người dùng ⇒ Backend enforce RBAC/ownership (vd supplier chỉ sửa sản phẩm của mình).

"Tôi là admin"/prompt injection được gắn cờ `security.injection_suspected` trong audit và không thay đổi quyền.

## 8. Audit

`app/audit.py` — JSONL append-only (`AUDIT_LOG_PATH`) + logger `woodhub.audit`. Sự kiện: `action.proposed|confirmed|completed|unverified|failed|cancelled|expired`, `permission.denied`, `security.injection_suspected`. Trường: timestamp, request_id, user_id, role, action, action_id, tool, target, before, after, status, confirmation, error, session_id. Khóa chứa token/password/secret/key/authorization bị `[REDACTED]`.

## 9. Known limitations

- Pending action, session, rate limit, identity cache là **in-memory** ⇒ chạy 1 instance (Render hiện tại). Nhiều instance cần Redis/DB cho `InMemoryActionRepository` và `SessionStore`.
- Audit trên đĩa của Render là tạm thời ⇒ cần Audit API của Backend (GAP B.9) hoặc log drain.
- Tra SKU phải quét tối đa `SKU_SCAN_MAX_PRODUCTS` sản phẩm (GAP B.2).
- Luồng ghi thật (PUT/PATCH/POST) **chưa được chạy với production** vì không có tài khoản test; request body được viết theo OpenAPI snapshot.
- Planner rules hiểu các mẫu câu phổ biến; câu tự do phức tạp cần `AGENT_PLANNER=llm` với model hỗ trợ tool use (Gemma 3 4B đang có trong `.env` chưa được xác minh hỗ trợ tool use).
