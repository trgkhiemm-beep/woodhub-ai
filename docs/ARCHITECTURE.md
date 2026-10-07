# WoodHub AI Agent — Architecture (v2.2 — customer-facing, read-only, no user auth, stabilized)

> Tài liệu khớp với code trên nhánh `feature/ai-agent`. Contract: `docs/FRONTEND_INTEGRATION.md`, `docs/BACKEND_INTEGRATION.md`,
> `contracts/agent-api.openapi.json`.

Agent là **trợ lý cho khách hàng của sàn nội thất WoodHub (nhiều nhà cung cấp)**: hiểu câu hỏi → tìm → truy xuất → so sánh →
tư vấn → trả lời, **chỉ đọc** dữ liệu thật. Mọi thay đổi dữ liệu (giá, tồn kho, mô tả, danh mục, khuyến mãi, FAQ) do
Admin/Supplier làm qua Backend Admin API — không qua agent.

| Thành phần | Trách nhiệm |
|---|---|
| **WoodHub Backend** | AUTH + USER ACCESS CONTROL: đăng nhập/đăng ký, phát hành & kiểm tra JWT, role/permission, quyết định ai được gọi chat (khách `/api/ai-chat`, admin `/api/admin/ai-agent`) và ai được xem dữ liệu riêng |
| **AI Agent** | NATURAL LANGUAGE + RETRIEVAL + RECOMMENDATION + ORCHESTRATION — **không** xác thực người dùng, **không** đọc JWT, **không** phân quyền admin/supplier/customer, **không** tự chặn người chưa đăng nhập |
| **Supabase** | SOURCE OF TRUTH (Agent chỉ đọc qua Backend API) |

```text
Customer → Frontend / App → WoodHub Backend → AI Agent → Backend APIs (GET) → Supabase
                              (auth, quota,      (understand, search,
                               business logic,    retrieve, compare,
                               CRUD, lưu chat)    recommend, answer)
```

## 1. Quyết định chính

| ID | Quyết định | Lý do |
|---|---|---|
| DEC-1 | Một agent hướng khách hàng. `/v1/agent/chat` và `/v1/agent/manage/chat` (Backend `/api/admin/ai-agent/chat` gọi tới) là **cùng một trợ lý chỉ đọc**; Agent không phân biệt người gọi | Backend đã tích hợp `/manage`; giữ route để không phá integration |
| DEC-12 | **Không có xác thực/phân quyền người dùng trong Agent** (v2.1): không verify JWT, không đọc role, không 401/403 vì người dùng. `Authorization` do Backend gửi kèm chỉ được chuyển tiếp NGUYÊN TRẠNG khi gọi lại Backend cho dữ liệu riêng; Backend quyết định | một nguồn auth duy nhất (Backend), tránh lệch cấu hình secret/thuật toán |
| DEC-13 | Bảo vệ dịch vụ bằng khóa server-to-server tùy chọn `AGENT_SERVICE_API_KEY` (header `X-Agent-Api-Key`) + mạng nội bộ; không dùng user JWT | Agent không public trần khi bật khóa |
| DEC-2 | **Agent CHỈ ĐỌC**: không có tool ghi, allowlist Backend chỉ có GET; yêu cầu sửa/xóa/tạo dữ liệu → câu trả lời cố định "chỉ hỗ trợ tra cứu" | Supplier tự quản lý giá/sản phẩm; Backend là authorization boundary chính |
| DEC-3 | `/v1/agent/actions/{id}` (GET/confirm/cancel) **giữ chữ ký** cho Backend; vì không còn action: GET → 404, confirm/cancel → 200 `type=error`, `ACTION_NOT_FOUND` | backward compatibility |
| DEC-4 | **Thông tin theo NHÀ CUNG CẤP**: hotline/email/khu vực lấy theo supplier của sản phẩm hoặc ngữ cảnh; không có "thông tin cửa hàng chung" | WoodHub là sàn nhiều nhà cung cấp |
| DEC-5 | **Backend là source of truth duy nhất**; AI service không truy cập DB, không giữ secret Supabase ở runtime | an toàn, không trùng lặp |
| DEC-6 | **NLU lai, deterministic trước**: domain guard + bộ luật; LLM (Bedrock Converse, Gemma 3 4B) chỉ khi bộ luật không chắc, chỉ phân loại ý + tách câu; code trích xuất mọi giá trị | tiết kiệm quota, không bịa entity |
| DEC-7 | Không có dữ liệu → câu cố định; không dùng kiến thức của model cho sản phẩm/nhà cung cấp/chính sách (`app/domain/messages.py`) | chống hallucination |
| DEC-8 | Không có policy engine giao hàng/đổi trả/bảo hành (thuộc từng nhà cung cấp; Backend chưa có dữ liệu) — không hứa thời gian/phí ship | đúng nghiệp vụ |
| DEC-9 | Khuyến mãi: không còn tool/logic (Backend/Supabase chưa có dữ liệu) → "chưa cập nhật thông tin" | không tạo business logic mới |
| DEC-10 | Câu trả lời ngắn `- Tên — 1.990.000đ`, 3–5 sản phẩm, chỉ trường được hỏi; dữ liệu đầy đủ trong `blocks` | đọc nhanh, ít token |
| DEC-11 | Không dữ liệu mock trong runtime lẫn test | yêu cầu chủ dự án |

Câu cố định (`app/domain/messages.py`):

| Trường hợp | Câu trả lời |
|---|---|
| Ngoài phạm vi WoodHub | `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ trên WoodHub.` |
| Không có dữ liệu phù hợp | `Hiện hệ thống chưa cập nhật thông tin phù hợp.` |
| Backend lỗi/timeout/dữ liệu hỏng | `Hiện hệ thống chưa thể kiểm tra thông tin này.` |
| Backend trả 429 cho Agent | `Hệ thống WoodHub đang bận, vui lòng thử lại sau ít phút.` (`error.code=UPSTREAM_RATE_LIMITED`) |
| Rate limit của chính Agent (HTTP 429) | `Trợ lý AI đang nhận quá nhiều yêu cầu, vui lòng thử lại sau ít phút.` (header `X-RateLimit-Layer: ai-agent`) |
| Yêu cầu thay đổi dữ liệu | `Trợ lý AI chỉ hỗ trợ tra cứu và tư vấn, không thay đổi dữ liệu. Vui lòng cập nhật qua trang quản trị của WoodHub.` |
| Chưa có nguồn (chính sách, giờ hoạt động, FAQ) | `Hiện chưa có thông tin đã xác minh về …` |

## 2. Luồng xử lý

```text
Backend ── agent-api v1 ──► app/api (khóa server-to-server tùy chọn, rate limit, alias camelCase; KHÔNG xác thực người dùng)
                                                    │
app/agent/orchestrator ── mỗi lượt ─────────────────┘
  1. NLU (app/nlu/engine)
       yêu cầu thay đổi dữ liệu (app/nlu/patterns.py) ─► CHANGE_REQUEST ─► trả lời "chỉ đọc", không tool, audit
       domain guard ─► ngoài phạm vi: từ chối ngay, KHÔNG gọi LLM, không gọi tool
       bộ luật tự tin ─► dùng luôn (≈92–97% lượt) · không chắc ─► LLM: intent + span ─► validate ─► đối chiếu
       entity (mã, tiền, số người, "mẫu 2", "cái này", rẻ/nhỏ hơn, tên nhà cung cấp, "shop này") ─► code trích xuất
  2. Với mỗi ý: Planner + DialogueState (memory)
       thiếu thông tin quan trọng ─► hỏi lại (không đoán); nhà cung cấp không xác định được ─► hỏi, gợi ý tên THẬT
  3. ToolExecutor: validate (Pydantic, extra=forbid) → timeout → tool CHỈ ĐỌC (gọi Backend kèm token chuyển tiếp nguyên trạng)
  4. Composer: câu trả lời CHỈ từ ToolResult + sources (provenance) ─► AgentResponse
                                                    │
app/ports.py (Protocol, chỉ đọc) ─► app/adapters/backend (allowlist 15 endpoint GET) ─► WoodHub Backend ─► Supabase
```

## 3. NLU có kiểm soát

Thử nghiệm với Gemma 3 4B trên Bedrock: native tool use không dùng được (model bỏ qua `toolConfig`); JSON có cấu trúc tốt
(~1 giây); entity không tin được (tự bịa mã). Vì vậy:

| Việc | Ai làm | File |
|---|---|---|
| Nhận diện yêu cầu thay đổi dữ liệu / SQL / "xác nhận ABC" | regex deterministic (không có parser tham số) | `app/nlu/patterns.py` |
| Domain guard | chủ đề ngoài phạm vi (thời tiết, chính trị, tin tức, thể thao, lập trình, toán, model/system prompt…) + tín hiệu thuộc WoodHub | `app/nlu/rules.py` |
| Intent + tách ý | bộ luật (`classify_conf` kèm độ tự tin); LLM chỉ khi không chắc → JSON `{intents:[{intent, span}]}`, span phải nằm trong câu gốc | `app/nlu/rules.py`, `app/nlu/llm.py`, `app/nlu/engine.py` |
| Giá trị entity | code: mã model/SKU, định danh dài (`TEST_..._987654321`), tiền, khoảng giá, số người, số thứ tự, tham chiếu, so sánh tương đối, danh mục/chất liệu/màu/phòng, **tên nhà cung cấp THẬT** (nạp từ Backend), "shop này", "khác nhà cung cấp", trường được hỏi | `app/nlu/extract.py`, `app/nlu/lexicon.py` |
| Chuẩn hóa giá | `2,5 triệu`, `2.5tr`, `2tr5`, `2 triệu 5`, `2 triệu rưỡi`, `2.500.000`, `2m5`, `2 củ`, `2 trịu/trẹo`, `trj`, `500k`; dưới/tầm/khoảng/từ…đến/`2-3tr`/trở xuống/hơn/`<`; `6 người`, `dài 2m`, `1m2-1m6` không phải tiền | `app/nlu/extract.py` |

Intent: `greeting, supplier_info, branches, policy, guide_faq, taxonomy, workshop, design_task, order_status, promotion,
product_search, recommend, product_detail, inventory, compare, change_request, cart, out_of_scope, unclear`.

## 4. Memory & ngữ cảnh (`DialogueState`)

| Trạng thái | Dùng cho |
|---|---|
| `shown` — danh sách vừa hiển thị | "mẫu 2", "so sánh mẫu 1 và 2" |
| `active` — sản phẩm đang nói tới | "cái này", "shop này ở đâu" (→ nhà cung cấp của sản phẩm), "chính sách đổi trả của shop này" |
| `constraints` — nhu cầu tích lũy | "10 triệu", "có mẫu nhỏ hơn không?" |
| `pending` / `asked` — câu hỏi làm rõ đang chờ | hỏi tối đa một lần cho mỗi loại |

Không lưu giá/tồn kho để trả lời lại, không lưu token. Session gắn chủ sở hữu, TTL 30 phút. Ngữ cảnh gửi LLM đã làm sạch.

## 5. Product Advisor (`recommend_products`)

Nhu cầu → **1 truy vấn** danh sách (lọc giá phía Backend; danh mục lá theo `categoryId`, cache 10 phút) → **ràng buộc cứng**
trên dữ liệu thật → (chỉ khi cần số chỗ/kích thước/màu) đọc chi tiết ≤8 sản phẩm → xếp hạng → tối đa 3 (tư vấn) / 5 (tìm).
- Loại mọi sản phẩm sai loại, ngoài ngân sách, sai chất liệu/màu, thiếu giá, thiếu dữ liệu cần thiết. Không nới ngân sách.
- `distinct_suppliers` ("từ các nhà cung cấp khác nhau"): mỗi nhà cung cấp tối đa 1 mẫu; dòng trả lời kèm tên nhà cung cấp.
- Mỗi item có `supplier`; so sánh (`compare_products`) có `supplier` từng dòng.
- Không còn sản phẩm → `Hiện hệ thống chưa cập nhật thông tin phù hợp.`

### 5.1 Tìm kiếm vs tư vấn (v2.2)

| Câu | Intent | `recommend_products` | Kết quả |
|---|---|---|---|
| "tìm bàn dưới 3 triệu", "có bàn học nào không", "tìm ghế gỗ", "Cho tôi 3 bàn" | `product_search` | `mode=search`, `limit` = số khách nêu (mặc định 5) | đúng điều kiện, **giá tăng dần** |
| "gợi ý bàn học phù hợp", "chọn giúp 3 mẫu", "bàn nào đáng mua", "bàn ăn 6 người dưới 10tr" | `recommend` | `mode=recommend`, `limit` mặc định 3 | xếp hạng theo nhu cầu (gần ngân sách, số chỗ…) |

Điều kiện parse deterministic: loại, giá min/max (kể cả "dưới 3m" = 3 triệu khi có tiền tố giá và không có từ kích thước),
chất liệu, màu, số chỗ, kích thước, **nhà cung cấp** (tên thật), **còn hàng** (điều kiện: đọc tồn kho thật; hết hàng → loại;
Backend không công khai → ghi "tồn kho: chưa có thông tin", không khẳng định còn hàng), **số lượng** ("3 bàn").

## 6. Tools (11, tất cả chỉ đọc)

| Tool | Loại | Quyền xem dữ liệu | Nguồn (Backend) |
|---|---|---|---|
| recommend_products | SEARCH | all | `GET /api/products`, `/api/products/{id}` |
| get_product | REALTIME | all | `GET /api/products/{id}` |
| compare_products | READ | all | `GET /api/products/{id}` |
| get_inventory | REALTIME | all (Backend chỉ trả cho supplier chủ) | `GET /api/variants/{id}/inventory` |
| get_supplier_info | READ | all | `GET /api/suppliers/{id}/public`, `/api/suppliers/{id}/stores`, `/api/suppliers/public` |
| list_branches | READ | all | `GET /api/suppliers/public`, `/api/suppliers/{id}/stores` |
| list_taxonomy | READ | all | `GET /api/categories|materials|rooms|styles` |
| search_knowledge (FAQ/hướng dẫn Web/App) | SEARCH | all | **chưa có nguồn** → "chưa có thông tin đã xác minh" |
| find_nearby_workshops | READ | Backend quyết định (API yêu cầu đăng nhập) | `GET /api/stores/nearby/workshops` |
| get_order_status | REALTIME | Backend quyết định (đơn của chủ token) | `GET /api/custom-orders/my`, `/api/custom-orders/{id}` |
| get_design_task_status | REALTIME | Backend quyết định | `GET /api/custom/ai/tasks/{id}` |

Registry từ chối khi khởi động mọi tool không phải READ/SEARCH/REALTIME và các tên cấm (`execute_sql`, `update_*`,
`create_promotion`, `adjust_inventory`, `delete_*`, `http_request`…). `BackendClient` chặn mọi method khác GET trước khi gửi.

### 6.1 So sánh, vị trí, tra mã (v2.2)
- `compare_products` trả bảng markdown: Giá · Kích thước · Chất liệu · Tồn kho · Nhà cung cấp; ô thiếu → "Chưa có thông tin".
- Vị trí: nhận `location{lat,lng}` hoặc `lat`/`lng` cấp ngoài (đúng `AdminAiChatRequest` của Backend). (-90,-180) (giá trị mẫu
  Swagger sinh từ `minimum`) và (0,0) bị coi là **không có vị trí** → "Bạn hãy bật chia sẻ vị trí…" (không giả định).
- Tra mã không tồn tại: chỉ mục SKU→product_id dựng một lần (TTL 5 phút, không chứa giá/tồn kho) → lần sau 1 request thay vì ~30.

## 7. Truy cập & audit

- **Agent không có lớp phân quyền người dùng.** Mọi tool chỉ đọc và giống nhau cho mọi người gọi. Dữ liệu riêng (đơn hàng,
  task 3D, xưởng gần) chỉ trả khi **Backend** chấp nhận token mà Backend gửi kèm; Backend từ chối → Agent nói
  "Hệ thống WoodHub chưa cho phép xem thông tin này…" (không tự tạo "Bạn cần đăng nhập"). Tồn kho bị Backend từ chối →
  "chưa có dữ liệu tồn kho".
- **Ai được gọi Agent**: Backend quyết định (chỉ admin tới được `/api/admin/ai-agent/*`). Agent bảo vệ server-to-server bằng
  `AGENT_SERVICE_API_KEY` (tùy chọn) và rate limit theo IP client (chống lạm dụng; quota người dùng do Backend).
- "Tôi là admin" trong tin nhắn bị gắn cờ `security.injection_suspected` và không thay đổi gì (không có gì để nâng quyền).
- **Audit** (`app/audit.py`, JSONL): `security.injection_suspected`, `agent.change_request_refused`; token/password/secret bị
  `[REDACTED]`; không log header `Authorization`.

### 7.1 Rate limit — xác định đúng tầng (v2.2)

| Tầng | Khi nào | Agent làm gì |
|---|---|---|
| Backend quota `ai_chat` | khách hết lượt chat | Backend trả 429 cho client; không tới Agent |
| **Agent** (HTTP 429 `RATE_LIMITED`) | vượt `RATE_LIMIT_PER_MINUTE` theo IP client | header `X-RateLimit-Layer: ai-agent`, `Retry-After: 30`; log `rate_limited layer=agent` |
| Backend API → Agent (429) | Backend giới hạn tool của Agent | **không retry** (retry ngay chỉ làm nặng thêm); `error.code=UPSTREAM_RATE_LIMITED`; log `layer=backend` |
| AWS Bedrock (throttling) | chỉ khi bộ luật không chắc và gọi LLM | không lộ cho khách: NLU chuyển sang bộ luật; log `layer=bedrock-throttled`; boto3 retry tối đa 2 |

Root cause `RATE_LIMITED` khi test qua Backend (bản cũ): limiter của Agent **30 request/phút theo IP**; sau Backend mọi người dùng
chung một IP → cả hệ thống chỉ được 30 tin/phút. v2.1+ mặc định 600/phút (cấu hình được). Tool sản phẩm
(`recommend_products`, tìm kiếm, so sánh) **không gọi LLM**; retry Backend chỉ cho timeout/5xx, tối đa `BACKEND_MAX_RETRIES` (2).

## 8. Source of truth & provenance

| Dữ liệu | Nguồn | Ghi chú |
|---|---|---|
| Giá, biến thể, tồn kho, đơn hàng, task 3D | Backend, đọc trong lượt | không cache; "rẻ hơn/nhỏ hơn" đọc lại sản phẩm tham chiếu |
| Danh mục, chất liệu, hồ sơ nhà cung cấp | Backend | cache ngắn (hồ sơ, không phải giá); tên nạp vào từ vựng NLU |
| Chính sách NCC, giờ hoạt động, FAQ Web/App, khuyến mãi | chưa có trên Backend/Supabase | "chưa có thông tin đã xác minh" / "chưa cập nhật thông tin" |

Mỗi response có `sources[]`: `system`, `resource`, `freshness`, `fetched_at`, `record_id`, `version`, `verified`.

## 9. Bảo mật (đã kiểm thử)

| Mối đe dọa | Biện pháp | Test |
|---|---|---|
| Ghi dữ liệu trái phép | không có tool ghi; allowlist chỉ GET; registry fail-fast | `test_agent_is_read_only`, `test_writes_are_blocked_before_any_request`, `test_change_requests_refused_for_every_caller` |
| Prompt injection / fake admin | không có quyền để nâng; agent chỉ đọc; LLM chỉ phân loại | `test_injection_and_fake_admin`, `test_guest_and_fake_admin_get_identical_answers` |
| User JWT giả/lạ | Agent không dùng JWT để quyết định gì; chuyển tiếp nguyên trạng, Backend tự kiểm tra | `tests/test_no_user_auth.py` |
| Gọi Agent trực tiếp từ ngoài | `AGENT_SERVICE_API_KEY` (tùy chọn) + mạng nội bộ | `test_service_key_is_optional_server_to_server_protection` |
| Tool abuse / tham số lạ / SQL | `extra=forbid`, allowlist, không có SQL | `test_endpoint_allowlist_blocks_arbitrary_calls` |
| Bịa sản phẩm / bỏ qua nguồn dữ liệu | composer chỉ dùng ToolResult; eval kiểm mọi số tiền | `test_cannot_bypass_data_source_or_invent`, eval `hallucination_rate` |
| Lộ dữ liệu / secret | lỗi không lộ chi tiết; redaction; session gắn chủ | `test_audit_redacts_secrets`, `test_no_token_or_secret_in_logs` |

## 10. Agent Evaluation

Bộ câu thực tế trên **dữ liệu thật** (`tests/eval/`): `cases` (105), `holdout` (25), `holdout2` (20); nhóm `read_only`
thay cho nhóm mutation cũ. Chạy `python -m tests.eval.run --mode rules|llm --suite …`.

### 10.1 v2.0 (customer-facing, read-only) — đo thật, Backend thật + Bedrock Gemma 3 4B

| Bộ (số câu) | Chế độ | Task completion | Intent | Tool | Trung thực khi thiếu dữ liệu | Hallucination | Trái phép | LLM call / token vào | Truy vấn Backend | Latency TB |
|---|---|---|---|---|---|---|---|---|---|---|
| cases (105) | rules | 100% | 100% | 100% | 100% | 0% | 0% | 0 / 0 | 1,11 | 195 ms |
| cases (105) | LLM | 100% | 100% | 100% | 100% | 0% | 0% | 0,029 / 10,4 | 1,11 | 237 ms |
| holdout (25) | rules / LLM | 100% / 100% | 100% | 100% | 100% | 0% | 0% | 0 · 0,08 / 28,8 | 1,56 | 246 / 394 ms |
| holdout2 (20) | rules / LLM | 100% / 100% | 100% | 100% | 100% | 0% | 0% | 0 · 0,05 / 18,6 | 1,65 | 337 / 334 ms |

Đo ngày 2026-10-06 trên Backend thật (đã thức); `pytest` v2.1: 304 passed. E2E HTTP thật (uvicorn, request đúng hình dạng Backend,
không user JWT): 13/13 (v2.1).

`cases`/`holdout` đã được dùng để sửa lỗi; `holdout2` là thước đo tổng quát hóa (viết mới, gần như không sửa theo).

### 10.2 Lịch sử chi phí (`cases`, chế độ LLM)

| Chỉ số / request | v1.1 (LLM-first) | v1.2 (hardened) | v2.0 |
|---|---|---|---|
| Lượt gọi LLM | 0,876 | 0,038 | 0,029 |
| Token vào | 627 | 13,9 | 10,4 |
| Truy vấn Backend | 3,91 | 1,14 | 1,11 |
| Latency TB | 3.305 ms | 191 ms | 237 ms |

## 11. Hạn chế đã biết

- FAQ Web/App, chính sách nhà cung cấp, giờ hoạt động, khuyến mãi: Backend/Supabase **chưa có nguồn** → agent chỉ nói chưa có.
- Tồn kho công khai chưa có (chỉ supplier chủ xem được) và `store_inventory` đang rỗng → "chưa có dữ liệu tồn kho".
- Tra một mã không tồn tại phải quét catalog (Backend chưa có API tra SKU).
- Session/rate limit ở **bộ nhớ trong** ⇒ 1 instance; restart mất ngữ cảnh hội thoại (không còn action chờ xác nhận nên không mất thao tác).
- Deterministic-first: câu bộ luật "tự tin" nhưng hiểu sai không được LLM sửa — theo dõi `meta.intents` trên log thật.
- "từ 5 triệu" (không có "đến") chưa hiểu là giá tối thiểu (trùng "tủ 5 triệu" sau khi bỏ dấu).
- `get_order_status`/xưởng gần/task 3D chỉ có dữ liệu khi Backend chuyển tiếp token của khách ở luồng `/api/ai-chat` (chưa xác minh).
- Session không gắn danh tính người dùng (Agent không biết người dùng): `session_id` ngẫu nhiên do server tạo; Backend phải giữ
  ánh xạ phiên ↔ người dùng và không lộ `session_id` của người này cho người khác.
- `AGENT_SERVICE_API_KEY` mặc định tắt (tương thích Backend hiện tại); khi chưa bật, nên giới hạn mạng để chỉ Backend gọi được Agent.

## 12. Thay đổi lớn

- **v2.2**: tách tìm kiếm/tư vấn; số lượng, lọc nhà cung cấp, điều kiện còn hàng; "dưới 3m"; "tủ 3 ngăn"/"tủ 2 cánh";
  "Shop của … ở đâu"; bảng so sánh có tồn kho; vị trí cấp ngoài + bỏ giá trị mẫu Swagger; rate limit theo tầng, không retry 429;
  chỉ mục SKU; thông điệp lỗi API thống nhất.
- **v2.1**: bỏ xác thực/phân quyền người dùng khỏi Agent (verify JWT, `/api/users/me`, role, 401/403, "cần đăng nhập");
  token chuyển tiếp nguyên trạng; thêm khóa server-to-server tùy chọn.
- **v2.0**: bỏ toàn bộ mutation (tool ghi, PendingAction/xác nhận, parser lệnh ghi, PUT/PATCH/POST trong allowlist), bỏ
  `get_store_info`/`get_promotions`/`get_policy`; thêm `get_supplier_info`, `get_order_status`, gợi ý đa nhà cung cấp,
  alias camelCase cho request, block `supplier_info`/`order_status`.
- **v1.2**: domain guard, deterministic-first, chuẩn hóa giá, câu trả lời ngắn, advisor lọc chặt.
- **v1.0** (so với chatbot gốc): bỏ truy cập Supabase trực tiếp bằng secret key, pipeline if/else, business_engine, Meshy trực
  tiếp, RAG FAISS chưa chạy, crawler, dead code.
