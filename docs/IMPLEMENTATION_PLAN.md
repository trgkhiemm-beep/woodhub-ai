# WoodHub AI — Implementation Plan (rev 4 — trạng thái sau Phase 2)

> Rev 3: sau khi có Swagger Backend — nhiều tool READ và admin dùng được API có sẵn (BACKEND Phần A), nên CP4 và một phần CP7 **không còn bị chặn**. Việc còn chặn: xác thực JWT (B-1), hợp đồng Backend→AI (B-2), giỏ hàng (B-4), promotion/policy/FAQ/audit (B-6).

> Rev 2: điều chỉnh theo repo git thật. `.env` chưa từng bị commit → không cần rotate khẩn; CP0 tập trung vào giảm quyền và dọn dead code thật.

> Kế hoạch cho Phase 2+. Chưa thực hiện. Mỗi checkpoint kết thúc bằng PROGRESS REPORT theo format đã thống nhất.
> Nguyên tắc: tiến hóa từ code hiện tại (REUSE/MODIFY trong AUDIT_REPORT), không rewrite; phần phụ thuộc Backend được cô lập sau interface để không block toàn bộ.

## Trạng thái thực hiện (2026-09-30, nhánh `feature/ai-agent`)

| CP | Nội dung | Trạng thái | Ghi chú |
|---|---|---|---|
| 1 | Repository audit | DONE | docs/AUDIT_REPORT.md |
| 2 | Architecture refactor (ports/adapters/agent/tools) | DONE | docs/ARCHITECTURE.md |
| 3 | Knowledge layer | DONE (phần có nguồn) | sản phẩm/danh mục/chi nhánh qua Backend; policy/FAQ/promotion chờ GAP B.6/B.7 → trả UNKNOWN |
| 4 | Tool layer (21 tool typed) | DONE | |
| 5 | Permission + validation | DONE | 2 lớp: agent + Backend RBAC |
| 6 | Confirmation (state machine) | DONE | |
| 7 | Backend adapter | DONE | 20 endpoint thật; GAP → CapabilityUnavailable |
| 8 | Frontend contract | DONE | agent-api v1 + OpenAPI |
| 9 | Security | DONE | docs/SECURITY_REVIEW.md §D |
| 10 | Tests | DONE | 133 pass (unit + live dữ liệu thật) |
| 11 | Cleanup | DONE | 25 file legacy xóa — docs/CHANGELOG.md |
| 12 | Final review | DONE | |

Việc tiếp theo (phụ thuộc bên ngoài): Backend B-1..B-7 + GAP B.1–B.10; tài khoản test để kiểm thử ghi thật; Frontend chuyển sang `/v1/agent/*`; Redis khi scale nhiều instance; đánh giá model Bedrock hỗ trợ tool use để bật `AGENT_PLANNER=llm`.

> Phần dưới là kế hoạch gốc (giữ để tham chiếu). Lưu ý: kế hoạch gốc có "Option T — đọc Supabase read-only" và mock adapter; cả hai **không** được dùng (DEC-5, DEC-11).

## Sơ đồ phụ thuộc

```text
CP0 Security hygiene ─▶ CP1 Test harness ─▶ CP2 Agent core ─▶ CP3 Guard + Auth ─┬─▶ CP4 Data layer (read tools) ─▶ CP6 Customer Agent
                                                                                 │                                   │
                                                                                 ├─▶ CP5 Knowledge/RAG ──────────────┤
                                                                                 │                                   ▼
                                                                                 └─▶ CP7 Admin Agent (propose/confirm) ─▶ CP8 Audit + Observability
                                                                                                                         ▼
                                                                                                CP9 Security hardening ─▶ CP10 Eval, Deploy, Docs
```

| CP | Tên | Phụ thuộc Backend? | Có thể bắt đầu ngay? |
|---|---|---|---|
| 0 | Hygiene & quick fixes | Phối hợp cấp key quyền hẹp | **Có** |
| 1 | Test harness & baseline | Không | Có |
| 2 | Agent core | Không | Có |
| 3 | Guard pipeline + AuthContext | JWT format (B2) cho verifier thật | Có (verifier là interface) |
| 4 | Data layer + read tools | Phần lớn đã có API | **Có** (sản phẩm, danh mục, phòng, xưởng, 3D); tồn kho/SKU/platform info chờ GAP |
| 5 | Knowledge / RAG | Bảng knowledge + search (B6) | Một phần — đánh giá embedding, retriever interface |
| 6 | Customer Agent hoàn chỉnh | Cart API, chat history API | Một phần |
| 7 | Admin Agent | Endpoint admin đã có; promotion/knowledge/moderation là GAP | **Có** cho danh mục/vật liệu/phòng/phong cách/supplier status/quota (xác nhận Phương án 2) |
| 8 | Audit + Observability | Audit API | Một phần (structured log, trace) |
| 9 | Security hardening | Không | Có |
| 10 | Eval, deploy, docs | Không | Có |

---

## Checkpoint 0 — Hygiene & quick fixes
- Thêm `.env.example` đủ biến (không giá trị thật); giữ `.gitignore` hiện có; bật GitHub secret scanning.
- IAM user/role chỉ `bedrock:InvokeModel*` cho model được phép; Meshy key giới hạn chi tiêu nếu nhà cung cấp hỗ trợ.
- Xin Backend/chủ DB một key/role **read-only** cho catalog thay cho secret key (S1) — nếu chưa có, ghi nhận rủi ro.
- Xóa dead code [VERIFIED không được import]: `app/orchestrator/*`, `services/intent_router.py`, `services/prompts.py`, `models/chat.py`, biến `GROQ_*`/`OLLAMA_*`; bỏ `desktop.ini` khỏi git.
- Sửa lỗi nhỏ không phụ thuộc Backend: bỏ nhánh giỏ hàng theo chuỗi con `"gio"` (D1); `/api/products` lọc `status=active` (S5); CORS không mặc định `"*"` (S6); giới hạn/TTL cho `session_memory` (S9).
- Cập nhật `HUONG_DAN_TEST.md` (đang tham chiếu `test_bedrock.py` không tồn tại).
- Pin dependency; thêm `pytest`, `pydantic-settings`; tách dependency của script (`requests`, `sentence-transformers`, `faiss-cpu`, `pandas`, `beautifulsoup4`) ra file riêng.
- **Done khi**: app chạy như cũ, các lỗi trên có test.

## Checkpoint 1 — Test harness & baseline
- pytest + fixtures mock Bedrock (trả tool_use giả lập) + mock data source.
- Chuyển `test_system_suite.py`, `test_normalization_suite.py` thành pytest có assert; mock `BedrockService` và Supabase để chạy offline.
- Ghi test "hành vi hiện tại" cho `/chat` để phát hiện regression khi refactor.
- **Done khi**: `pytest` chạy offline, không gọi mạng.

## Checkpoint 2 — Agent core
- `config.py` → pydantic-settings (model id theo agent, timeouts, giới hạn).
- LLM client dùng **Bedrock Converse API** (thay body tự viết cho từng provider trong `bedrock_service.py`), hỗ trợ tool use + streaming.
- Nâng model từ `google.gemma-3-4b-it` (hiện tại): Claude Haiku 4.5 cho Customer, model mạnh hơn cho Admin — **xác minh model id / inference profile khả dụng ở region đang dùng trong AWS console** trước khi cấu hình.
- `ToolSpec` + `ToolRegistry` + `ToolResult` có cấu trúc; schema từ Pydantic (`extra="forbid"`).
- Orchestrator với `AgentProfile` (prompt, model, registry, giới hạn vòng/tool call/timeout).
- Router mới `/v1/customer/chat`; `/chat` giữ nguyên request + định dạng SSE hiện tại (FRONTEND §9) và gọi vào Customer Agent.
- Giữ fast path deterministic hiện có (lời chào, mơ hồ, ngoài phạm vi rõ ràng) trước agent.
- Sửa classifier: token/phrase matching, thêm intent STORE/POLICY/FAQ/PROMOTION/APP_GUIDE; chỉ dùng định tuyến.
- **Done khi**: các chức năng hiện có (tìm sản phẩm, cửa hàng gần, báo giá đặt làm, gợi ý xưởng) chạy như tool qua registry mới; test registry invariants pass.

## Checkpoint 3 — Guard pipeline + AuthContext
- `AuthContext{user_id, role, is_guest}`; `TokenVerifier` interface; implementation thật chờ BLOCKER B2 (JWT).
- Guard: schema validation → identity injection (ghi đè) → permission → business validation.
- CORS whitelist, giới hạn độ dài input, rate limit cơ bản.
- **Done khi**: test "customer không gọi được tool admin", "LLM truyền user_id bị bỏ qua", "guest không có cart tool".

## Checkpoint 4 — Data layer + read tools
- `DataSource` interface; `BackendDataSource` (httpx) gọi **API Backend có sẵn** (BACKEND Phần A.2) với allowlist endpoint; map `PagedModel`, lỗi Spring (403 khi chưa đăng nhập).
- Khôi phục dấu tiếng Việt cho `keyword` trước khi gọi `/api/products` (Backend không khớp không dấu).
- Option T (chỉ còn cần cho dữ liệu chưa có API) : `SupabaseReadOnlyDataSource` dùng **role read-only**, chỉ SELECT bảng catalog, để triển khai read tools trước khi Backend có API. Không phải mock; bị gỡ khi Backend sẵn sàng.
- Tools: `get_platform_info`*, `list_showrooms`, `find_nearby_stores`, `search_products`, `get_product`, `get_availability`, `get_active_promotions`*, `get_policy`*, `estimate_custom_price`, `match_workshops`* (\* chờ Backend tạo dữ liệu/API).
- `search_products` dùng tìm kiếm phía server (`fts_vector`) thay cho tải toàn bộ catalog; giá hiển thị theo khoảng variant; `availability` thật hoặc `unknown`.
- Tham số báo giá đặt làm lấy từ nguồn nghiệp vụ; workshop bỏ dữ liệu mock.
- **Done khi**: contract test với mock server theo spec pass; không còn dữ liệu bịa.

## Checkpoint 5 — Knowledge / RAG
- Đánh giá embedding tiếng Việt (Titan Text Embeddings V2, Cohere Embed Multilingual trên Bedrock, và `keepitreal/vietnamese-sbert` 768 chiều đang có trong repo/khớp cột `product_variants.embedding`) với bộ ~50 câu hỏi FAQ thực tế.
- Thay `app/rag/*` (FAISS local) và sửa/thay `scripts/sync_data.py` (đang select cột không tồn tại, ghi DB bằng secret key).
- Chunker (theo heading, ~500 token, overlap), `Retriever` interface, `search_knowledge` tool, trích dẫn nguồn.
- Webhook reindex (HMAC) theo Option K1/K2 đã chọn.
- **Done khi**: recall@5 ≥ ngưỡng thống nhất trên bộ eval; câu trả lời policy có `sources`.

## Checkpoint 6 — Customer Agent hoàn chỉnh
- Cart tools: **tạm gỡ** — Backend không có API giỏ hàng (B-4).
- Tích hợp với `POST /api/ai-chat/sessions/{id}/messages` của Backend theo hợp đồng chốt ở B-2 (endpoint nội bộ `/internal/v1/agent/customer/turn`, service token).
- 3D từ ảnh: dùng `/api/custom/ai/generate` + `/api/custom/ai/tasks/{id}` của Backend (đã có quota); **gỡ `meshy_service.py`** khỏi AI service; bỏ giá cứng 1.200.000.
- Memory: thay `session_memory` (dict RAM) bằng chat history qua Backend (đã có `chat_sessions`/`chat_messages`) hoặc store có TTL cho guest; lưu cả tool call metadata.
- Response có cấu trúc + SSE streaming.
- Prompt Customer mới (tiếng Việt có dấu, grounding rules, untrusted-data rules).
- **Done khi**: kịch bản READ/SEARCH/cart end-to-end với mock Backend pass.

## Checkpoint 7 — Admin Agent
- `/v1/admin/chat`, AdminProfile, admin read tools.
- Đợt 1 (không chờ Backend): `propose_*` cho danh mục, vật liệu, phòng, phong cách, phòng mẫu, gắn sản phẩm, trạng thái supplier, hạn mức user → pending action token HMAC (BACKEND B.10 Phương án 2) → execute bằng endpoint admin **đã có** với JWT admin; audit tạm ghi log có cấu trúc.
- Đợt 2 (khi Backend có GAP B.1/B.6/B.7/B.8/B.10): platform info, promotion, policy/FAQ, moderation; chuyển sang agent-actions của Backend.
- `/v1/admin/actions/{id}/confirm|cancel` (code thuần): verify owner/TTL/phrase → execute → **verification** (đọc lại, so sánh) → kết quả.
- Từ chối có giải thích cho yêu cầu ngoài quyền (đổi giá/tồn kho/mô tả supplier).
- **Done khi**: test "LLM không thể execute", "confirm bởi admin khác bị từ chối", "version conflict", "mass update bị chặn".

## Checkpoint 8 — Audit + Observability
- Gửi audit event (từ chối quyền, injection nghi vấn, cancel) tới Backend; mutation audit do Backend ghi trong transaction.
- Structured JSON log, `request_id` xuyên suốt, redaction PII.
- Metric: latency, token, chi phí ước tính, tool error rate, tỉ lệ `unknown` availability.

## Checkpoint 9 — Security hardening
- Red-team set (≥30 prompt: fake admin, injection trực tiếp/gián tiếp, mass update, data leakage).
- Quota `ai_chat`, rate limit theo user, giới hạn chi phí.
- `pip-audit`, secret scanning.

## Checkpoint 10 — Eval, deploy, docs
- Eval set tiếng Việt (có dấu/không dấu/teencode) cho 5 loại operation; LLM-judge + assert cứng cho grounding.
- Dockerfile (platform-agnostic), cấu hình Render (health check, env vars), hướng dẫn chuyển nền tảng.
- Cập nhật tài liệu: spec → API thật, README, runbook.

---

## Rủi ro chính

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Backend chậm cung cấp API | CP4–8 bị kéo dài | Option T cho read; contract test với mock theo spec; ưu tiên thống nhất `agent-actions` sớm |
| Dữ liệu store/promotion/policy chưa tồn tại | Agent không có gì để trả lời | Backend tạo bảng + seed; agent trả lời "chưa có thông tin" thay vì bịa |
| Tồn kho rỗng (`store_inventory` = 0) | Luôn `unknown` | Nhắc supplier nhập tồn kho; UI hiển thị "liên hệ" |
| Chất lượng embedding tiếng Việt | RAG kém | Đánh giá ở CP5 trước khi chốt |
| Render free tier (sleep, không Redis) | Cold start, mất session guest | Dùng gói có Redis hoặc lưu session qua Backend |
