# WoodHub AI Agent — Architecture (v1.2 — hardened)

> Tài liệu khớp với code trên nhánh `feature/ai-agent`. Contract: `docs/FRONTEND_INTEGRATION.md`, `docs/BACKEND_INTEGRATION.md`.

## 1. Quyết định chính

| ID | Quyết định | Lý do |
|---|---|---|
| DEC-1 | Hai agent trên một core: Customer (`/v1/agent/chat`) và Management (`/v1/agent/manage/chat`) | cùng orchestrator, khác profile/tool |
| DEC-2 | Role theo Backend: `guest`, `customer`, `supplier`, `admin`. Supplier chỉ có tool trên dữ liệu của mình | tôn trọng RBAC thật |
| DEC-4 | Admin không sửa giá/tồn kho/mô tả sản phẩm của supplier | Backend chỉ cho supplier chủ |
| DEC-5 | **Backend là source of truth duy nhất**; AI service không truy cập DB | an toàn, không trùng lặp |
| DEC-6 | **NLU lai có kiểm soát**: LLM (Bedrock Converse, Gemma 3 4B) phân loại ý + tách câu; code trích xuất mọi giá trị | xem §3 |
| DEC-9 | Mọi mutation cần xác nhận gắn action_id + mã | không thực thi nhầm |
| DEC-11 | Không dữ liệu mock trong runtime lẫn test | yêu cầu chủ dự án |
| DEC-12 | Backend chưa có dữ liệu ⇒ câu cố định "Hiện hệ thống chưa cập nhật sản phẩm phù hợp." / "chưa có thông tin đã xác minh"; Backend lỗi ⇒ "Hệ thống chưa thể kiểm tra dữ liệu sản phẩm lúc này." (`app/domain/messages.py`) | không bịa, phân biệt rõ 3 trường hợp |
| DEC-13 | **Domain guard + deterministic-first**: câu ngoài phạm vi bị từ chối bằng luật trước khi tới LLM; LLM chỉ được gọi khi bộ luật không chắc chắn | tiết kiệm quota, không trả lời lệch phạm vi |
| DEC-14 | Câu trả lời ngắn: `- Tên — 1.990.000đ`, chỉ trường được hỏi; chi tiết nằm trong `blocks` | đọc nhanh, ít token |

## 2. Luồng xử lý

```text
Frontend / Backend proxy ── contract agent-api v1 ──► app/api (verify JWT Backend HS256 tại chỗ, rate limit)
                                                            │
app/agent/orchestrator ── mỗi lượt ─────────────────────────┘
  1. NLU  (app/nlu/engine)
       xác nhận/hủy, lệnh thay đổi dữ liệu ─► parser deterministic (không qua LLM)
       domain guard (ngoài phạm vi) ─► từ chối ngay, KHÔNG gọi LLM, không gọi tool
       bộ luật tự tin ─► dùng luôn (≈95% lượt) · không chắc ─► LLM: intent + tách ý (span) ─► validate ─► đối chiếu
       mọi entity (mã, tiền, số người, "mẫu 2", "cái này", rẻ/nhỏ hơn…) ─► trích xuất bằng code từ span
  2. Với mỗi ý: Planner (app/agent/planner) + DialogueState (app/agent/dialogue)
       thiếu thông tin quan trọng ─► hỏi lại (không đoán)
       "rẻ hơn/nhỏ hơn" ─► đọc lại sản phẩm tham chiếu (realtime) ─► tìm theo ràng buộc mới
  3. ToolExecutor: permission → validate (Pydantic, extra=forbid) → timeout → READ | PROPOSE mutation
  4. Mutation: PendingAction ─(xác nhận đúng mã, đúng người, còn hạn)─► check_fresh ─► execute ─► verify ─► audit
  5. Composer: câu trả lời CHỈ từ ToolResult + sources (provenance) ─► AgentResponse
                                                            │
app/ports.py (Protocol) ─► app/adapters/backend (allowlist 20 endpoint thật) ─► WoodHub Backend ─► Supabase
```

## 3. NLU có kiểm soát (LLM + trích xuất deterministic)

Thử nghiệm thực tế với Gemma 3 4B trên Bedrock (model đang cấu hình):
- **Native tool use: không dùng được** — model nhận `toolConfig` nhưng bỏ qua, tự bịa câu trả lời.
- **JSON có cấu trúc: tốt** — luôn hợp lệ, ~1 giây/lượt, hiểu không dấu/teencode/tiếng Anh/nhiều ý.
- **Entity: không tin được** — tự bịa ("mẫu 2" → mã `MAU-02`, "bàn" → "tủ").

Thiết kế vì vậy:

| Việc | Ai làm | File |
|---|---|---|
| Xác nhận / hủy / lệnh thay đổi dữ liệu + tham số | parser deterministic | `app/nlu/mutations.py` |
| Intent + tách ý nhiều-ý | LLM → JSON `{intents:[{intent, span}]}`; span phải nằm trong câu gốc | `app/nlu/llm.py` |
| Giá trị entity | code: mã model/SKU, tiền (`10 củ`, `8tr5`, `500k`, `10 million`), số người, số thứ tự, tham chiếu, so sánh tương đối, danh mục/chất liệu/màu/phòng (từ vựng VI/không dấu/EN + tên danh mục THẬT từ Backend) | `app/nlu/extract.py`, `app/nlu/lexicon.py` |
| Domain guard | danh sách chủ đề ngoài phạm vi (thời tiết, chính trị, lập trình, toán, model/system prompt…) + tín hiệu thuộc cửa hàng; trả nguyên văn `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ của cửa hàng.` | `app/nlu/rules.py` |
| Chuẩn hóa giá | `2,5 triệu`, `2.5tr`, `2tr5`, `2 triệu 5`, `2 triệu rưỡi`, `2.500.000`, `2m5`, `2 trịu/trẹo`, `trj`, `củ`, `500k`; khoảng: dưới/tầm/khoảng/từ…đến/`2-3tr`/trở xuống/hơn/`<`; `6 người`, `dài 2m`, `1m2-1m6` không bị hiểu là tiền | `app/nlu/extract.py` |
| Đối chiếu / dự phòng | bộ phân loại dựa trên tín hiệu entity, trả kèm độ tự tin (`classify_conf`); ghi đè nhãn LLM yếu (vd `out_of_scope` khi có mã sản phẩm, `design_task` khi không có mã task) và dùng khi LLM lỗi | `app/nlu/rules.py`, `app/nlu/engine.py` |

LLM nhận ngữ cảnh đã làm sạch (danh sách vừa hiển thị, sản phẩm đang nói tới, câu hỏi đang chờ) — không nhận dữ liệu tool thô.

## 4. Memory & ngữ cảnh (`DialogueState`)

| Trạng thái | Dùng cho |
|---|---|
| `shown` — danh sách vừa hiển thị | "mẫu 2", "cái thứ ba", "so sánh mẫu 1 và 2" |
| `active` — sản phẩm đang nói tới | "cái này", "nó", "bàn này còn hàng không" |
| `constraints` — nhu cầu tích lũy (loại, ngân sách, số người, kích thước, chất liệu, màu, phòng, sở thích giá) | "10 triệu", "có mẫu nhỏ hơn không?" |
| `pending` / `asked` — câu hỏi làm rõ đang chờ, đã hỏi | hỏi tối đa một lần cho mỗi loại |

Không lưu giá/tồn kho để trả lời lại, không lưu token. Session gắn chủ sở hữu, TTL 30 phút, giới hạn số session.

## 5. Product Advisor (`recommend_products`)

Nhu cầu → **1 truy vấn** danh sách (lọc giá phía Backend, danh mục lá theo `categoryId`; danh mục lấy từ cache 10 phút) →
**ràng buộc cứng** trên dữ liệu thật → (chỉ khi tiêu chí cần: số chỗ/kích thước/màu) đọc chi tiết ≤8 sản phẩm → xếp hạng.
- Loại bỏ mọi sản phẩm sai loại, ngoài ngân sách (min/max), sai chất liệu/màu, thiếu giá, thiếu kích thước/số chỗ khi tiêu chí cần
  (không xác minh được = không gợi ý). **Không nới ngân sách, không gợi ý "gần đúng".**
- Không còn sản phẩm → `NOT_FOUND` → "Hiện hệ thống chưa cập nhật sản phẩm phù hợp.".
- Món không thuộc danh mục ("có đèn ngủ không") → tra theo tên trên catalog thật, không có → câu "chưa cập nhật".
- Số chỗ bàn ăn: lấy từ tên/mô tả ("6 ghế"), nếu không có thì **ước tính** theo chiều dài (ghi rõ).
- Phòng: dữ liệu `product_rooms` của Backend đang rỗng ⇒ dùng quan hệ "loại sản phẩm thường dùng cho phòng" (ghi rõ).
- Kích thước hiển thị **nguyên văn** dữ liệu; bản parse (mm/cm) chỉ dùng để so sánh.

## 6. Tools (21)

| Tool | Loại | Role | Xác nhận | Nguồn |
|---|---|---|---|---|
| recommend_products | SEARCH | all | — | `/api/products`, `/api/products/{id}` |
| get_product | REALTIME | all | — | `/api/products/{id}` |
| compare_products | READ | all | — | `/api/products/{id}` |
| get_inventory | REALTIME | all (thực tế: supplier chủ) | — | `/api/variants/{id}/inventory` |
| list_taxonomy | READ | all | — | `/api/categories|materials|rooms|styles` |
| list_branches | READ | all | — | `/api/suppliers/public`, `/api/suppliers/{id}/stores` |
| find_nearby_workshops | READ | đã đăng nhập | — | `/api/stores/nearby/workshops` |
| get_design_task_status | REALTIME | đã đăng nhập | — | `/api/custom/ai/tasks/{id}` |
| get_store_info, get_policy, search_knowledge, get_promotions | READ/SEARCH | all | — | **GAP** Backend (trả "chưa xác minh") |
| update_product_description | UPDATE | supplier | standard | `PUT /api/products/{id}` |
| update_product_price, adjust_inventory | SENSITIVE | supplier | strong | `PUT /api/variants/{id}`, `PATCH /api/stores/{sid}/inventory/{vid}` |
| upsert_category, upsert_material | UPDATE | admin | standard | `POST/PUT /api/categories|materials` |
| update_store_info, set_promotion_status | SENSITIVE | admin | strong | **GAP** |
| upsert_faq | UPDATE | admin | standard | **GAP** |
| create_promotion | ACTION | admin | strong | **GAP** |

Không tồn tại (kiểm tra khi khởi động): `execute_sql`, `update_anything`, `run_arbitrary_command`, `http_request`, `confirm_action`, `delete_*`.

## 7. Permission, xác nhận, audit

- **Permission 2 lớp**: `ToolRegistry.check` (profile × role × tool, kiểm tra trước validate, từ chối được audit) + Backend RBAC qua JWT của chính người dùng. "Tôi là admin" không đổi quyền; bị gắn cờ `security.injection_suspected`.
- **State machine**: `PENDING_CONFIRMATION → CONFIRMED → EXECUTING → VERIFIED → COMPLETED`, nhánh `CANCELLED | EXPIRED | FAILED | UNVERIFIED`. Xác nhận bằng `xác nhận <MÃ>` hoặc `POST /v1/agent/actions/{id}/confirm`; "ok" không thực thi; sai mã 5 lần → hủy; xác nhận lặp không ghi lần 2; dữ liệu đổi trong lúc chờ → `STALE_DATA`; timeout khi ghi → `UNVERIFIED` (không báo thành công).
- **Audit** (`app/audit.py`, JSONL): timestamp, request_id, user_id, role, action, action_id, tool, target, before, after, status, confirmation, error. Token/password/secret/key bị `[REDACTED]`.

## 8. Source of truth & provenance

| Dữ liệu | Nguồn | Ghi chú |
|---|---|---|
| Giá, biến thể, tồn kho, trạng thái task | Backend, đọc trong lượt | không cache; "rẻ hơn/nhỏ hơn" đọc lại sản phẩm tham chiếu |
| Danh mục, chất liệu, chi nhánh | Backend | từ vựng NLU nạp tên danh mục thật lúc khởi động |
| Policy, FAQ, khuyến mãi, giờ mở cửa, hotline | chưa có (GAP) | trả "chưa có thông tin đã xác minh" |

Mỗi response có `sources[]`: `system`, `resource`, `freshness` (realtime/reference/semantic), `fetched_at`, `record_id`, `version`, `verified`.

## 9. Bảo mật (đã kiểm thử)

| Mối đe dọa | Biện pháp | Test |
|---|---|---|
| Prompt injection / fake admin | quyền từ JWT; LLM chỉ phân loại, không chọn tham số; tool ngoài quyền không tồn tại với profile | `test_customer_and_fake_admin_cannot_mutate`, eval nhóm `security` |
| Privilege escalation | `/manage` chỉ admin/supplier (403); admin không sửa dữ liệu supplier | `test_guest_cannot_use_management_agent`, `test_admin_cannot_change_supplier_price` |
| Tool abuse / tham số lạ | `extra=forbid`, allowlist endpoint, giới hạn tool call/lượt | `test_endpoint_allowlist_blocks_arbitrary_calls`, `test_llm_cannot_inject_entities` |
| Confirmation bypass / replay | mã + chủ action + TTL + lock; LLM không bao giờ xử lý xác nhận | `test_confirmation_must_carry_code_and_match`, `test_confirmation_never_goes_through_llm` |
| Mass update | không có tool bulk; khuyến mãi ≤50%, ≤5 danh mục; ≤5 action chờ/người | `test_mass_discount_is_blocked`, `test_pending_action_limit` |
| Data / secret leakage | lỗi không lộ chi tiết; audit redaction; session gắn chủ; không có bí mật trong prompt | `test_audit_redacts_secrets`, `test_session_context_not_shared_between_users`, eval `sec-07` |
| Hallucination | composer chỉ dùng ToolResult; eval kiểm mọi số tiền trong câu trả lời có trong dữ liệu tool | eval `hallucination_rate` |

## 10. Agent Evaluation

Bộ câu thực tế trên **dữ liệu thật** (`tests/eval/`): `cases` (105 câu), `holdout` (25), `holdout2` (20). Chạy `python -m tests.eval.run --mode rules|llm --suite …`.

| Bộ | Chế độ | Task completion | Intent | Tool | Tham số | Ngữ cảnh | Gợi ý | Làm rõ | Hallucination | Trái phép | Latency TB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| cases | rules | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 0,44 s |
| cases | LLM (Gemma) | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 0% | 0% | 0,97 s |
| holdout — **trước** tinh chỉnh | rules / LLM | 84% / 80% | 100% / 100% | 87% / 83% | 100% / 89% | 67% / 100% | 100% / 86% | 100% / 100% | 0% | 0% | 0,5 / 1,2 s |
| holdout — sau tinh chỉnh | rules / LLM | 100% / 100% | | | | | | | 0% | 0% | |
| **holdout2 — chưa từng tinh chỉnh** | rules / LLM | **90% / 95%** | — | 95% / 100% | 100% / 100% | 100% / 100% | 83% / 83% | — | 0% | 0% | 0,41 / 1,0 s |

Cách đọc trung thực: `cases` và `holdout` đã được dùng để sửa lỗi nên con số 100% là trên tập đã thấy. **`holdout2` là thước đo tổng quát hóa** (viết mới, chạy một lần, không sửa theo).

### 10.1 Sau hardening (v1.2) — chi phí trước/sau (đo thật, Bedrock Gemma 3 4B + Backend thật)

| Chỉ số / request | Trước (v1.1, `cases`, LLM) | Sau (v1.2) `cases` | `holdout` | `holdout2` |
|---|---|---|---|---|
| Task completion (LLM / rules) | 99% | 100% / 100% | 100% / 100% | 100% / 100% |
| Hallucination / hành động trái phép | 0% / 0% | 0% / 0% | 0% / 0% | 0% / 0% |
| Lượt gọi LLM | 0,876 | **0,038** | 0,08 | 0,05 |
| Token vào / ra | 627 / 26,9 | **13,9 / 1,1** | 29,6 / 2,4 | 19,1 / 1,5 |
| Truy vấn Backend (DB) | 3,91 | **1,14** | 1,6 | 1,6 |
| Tool call | 0,93 | 0,92 | 0,96 | 1,0 |
| Latency TB | 3.305 ms | **191 ms** | 272 ms | 249 ms |

Nguồn tiết kiệm: domain guard + luật tự tin trước LLM; prompt NLU rút gọn (~720 → ~370 token/lượt gọi), `maxTokens` 300 → 150;
ngữ cảnh gửi LLM không lặp danh sách sản phẩm; tư vấn 1 truy vấn thay vì 2–3 tìm kiếm + 14 chi tiết; tra mã model đọc 1 chi tiết
thay vì 5; cache danh mục/chất liệu (giá/tồn kho **không** cache). `holdout2` sau v1.2 chưa được dùng để sửa luật, trừ một lỗi
được phát hiện khi chạy lại (tiếng Anh số nhiều "promotions").

## 11. Hạn chế đã biết

- Deterministic-first: câu bộ luật "tự tin" nhưng hiểu sai sẽ không được LLM sửa. Bộ đánh giá hiện không phát hiện trường hợp
  nào, nhưng cần theo dõi bằng log `meta.intents` trên dữ liệu thật.
- "từ 5 triệu" (không có "đến") chưa hiểu là giá tối thiểu vì sau khi bỏ dấu trùng với "tủ 5 triệu".
- Tư vấn quét 1 trang 50 sản phẩm (catalog thật có 27); khi catalog lớn hơn cần Backend hỗ trợ lọc theo tên/danh mục cha.
- Gemma 3 4B đôi khi gán nhầm intent ở câu hiếm; bộ dự phòng che phần lớn, nhưng câu tự do rất khác mẫu vẫn có thể bị hỏi lại.
- Pending action, session, rate limit ở **bộ nhớ trong** ⇒ 1 instance. Audit JSONL trên đĩa Render là tạm thời.
- Luồng ghi thật (PUT/PATCH/POST) chưa chạy với production (không có tài khoản test); request body theo OpenAPI snapshot.
- Policy/FAQ/khuyến mãi/giờ mở cửa/tồn kho công khai phụ thuộc Backend (GAP).

## 12. Thay đổi so với chatbot gốc

Đã xóa (revert được qua git): truy cập Supabase trực tiếp bằng secret key (`app/core/database.py`), pipeline if/else + classifier khớp chuỗi con, `business_engine` (tải toàn catalog, ghi giỏ hàng lỗi, công thức giá cứng), Meshy trực tiếp + giá 3D cứng, `/api/products` lộ draft, workshop mock, RAG FAISS chưa chạy + script sync lỗi, crawler website bên thứ ba, dead code (orchestrator/Groq/prompt cũ), LLM tool-use loop (Gemma không hỗ trợ), test script không assert. Giữ và tái sử dụng: bộ chuẩn hóa tiếng Việt (`app/nlp/vietnamese.py`).
