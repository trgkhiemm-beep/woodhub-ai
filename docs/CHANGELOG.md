# CHANGELOG

## [1.0.0] — 2026-09-30 — AI Store Knowledge & Management Agent

Nhánh: `feature/ai-agent` (từ `main@401617b`). Mọi thay đổi revert được qua git.

### Added
- **Agent core** `app/agent/`: orchestrator, rule planner, LLM planner (Bedrock Converse), tool executor, action service (state machine xác nhận), composer + grounding guard, session store.
- **Tools** `app/tools/`: 12 read tool + 9 mutation tool, typed (Pydantic, `extra=forbid`), registry kiểm tra bất biến an toàn khi khởi động.
- **Ports & adapters** `app/ports.py`, `app/adapters/backend/`: client HTTP có allowlist endpoint, timeout, retry cho GET/PUT, chuẩn hóa lỗi; adapter cho 20 endpoint Backend thật (theo OpenAPI snapshot).
- **Public API** `app/api/`: `/v1/agent/chat`, `/v1/agent/manage/chat` (+ `/stream` SSE), `/v1/agent/actions/{id}` (+ `/confirm`, `/cancel`), `/v1/agent/capabilities`, `/health`.
- **Audit log** `app/audit.py` (JSONL, redaction).
- **Contracts** `contracts/agent-api.openapi.json` (sinh từ app), `contracts/backend/woodhub-be.openapi.snapshot-2026-09-30.json`.
- **Tests** `tests/` — 133 test: unit + live trên dữ liệu thật (Backend + đối chiếu Supabase), chặn ghi production.
- `Dockerfile`, `.dockerignore`, `.env.example`, `requirements-dev.txt`, `README.md`.

### Changed
- `app/services/input_normalizer.py` → `app/nlp/vietnamese.py` (git mv, giữ nguyên logic; thêm `restore_diacritics_for_search` vì Backend chỉ khớp keyword có dấu; bỏ wrapper `normalize_input` không dùng).
- `app/main.py`: app factory, error envelope thống nhất, CORS chỉ bật khi có whitelist, không còn mặc định `*`.
- `app/config.py` (thay `app/core/config.py`): pydantic-settings, fail-fast (thiếu `BACKEND_BASE_URL`, `CORS=*` ở production, `llm` thiếu model).
- `POST /chat`: giữ request/SSE cũ nhưng là **adapter mỏng** vào Customer Agent (deprecated).
- `requirements.txt`: pin version; bỏ `supabase`.

### Deleted
| File / module | Lý do | Thay thế | Ảnh hưởng |
|---|---|---|---|
| `app/core/database.py` | AI truy cập Supabase trực tiếp bằng **secret key** (bypass RLS) + monkeypatch IPv4 toàn cục | `BackendClient` + adapters | không còn secret DB trong runtime |
| `app/services/business_engine.py` | tự query DB, tải toàn catalog để lọc, ghi `cart_items` (luôn lỗi NOT NULL), công thức giá đặt làm hard-code | tools + Backend `/api/products` | báo giá ước tính bị gỡ (không có nguồn nghiệp vụ; Backend có luồng báo giá xưởng `/api/quotes`) |
| `app/services/classifier.py` | khớp chuỗi con, xếp policy/voucher/giờ mở cửa là OUT_OF_SCOPE | `app/agent/planner.py` | |
| `app/services/bedrock_service.py` | body `invoke_model` theo từng provider, không tool use | `app/agent/llm.py` (Converse) | |
| `app/services/meshy_service.py`, `GET /api/3d/status` | trùng chức năng Backend (`/api/custom/ai/*` có auth + quota); gọi Meshy không auth; trả giá cứng 1.200.000 | Backend 3D API; tool `get_design_task_status` | `/chat` với `image_url` hướng người dùng sang Phòng thiết kế 3D |
| `app/api/products.py` (`GET /api/products`) | `select *` không lọc status (lộ draft/hidden) | Backend `/api/products` | client gọi thẳng Backend |
| `app/api/workshops.py`, `app/services/matching_service.py`, `app/models/matching.py` (`GET /api/workshops`, `POST /matching`) | dữ liệu xưởng **mock** cứng | tool `find_nearby_workshops` → `/api/stores/nearby/workshops` | client dùng agent hoặc Backend |
| `app/api/chat.py` | pipeline if/else cũ; nhánh giỏ hàng theo chuỗi con "gio" (hỏi "mấy giờ" ra giỏ hàng); `session_memory` RAM vô hạn | orchestrator + `api/legacy.py` | |
| `app/schemas/chat.py`, `app/models/chat.py` | DTO cũ/trùng tên | `app/api/schemas.py` | |
| `app/orchestrator/*`, `app/services/intent_router.py`, `app/services/prompts.py`, `app/services/__init__.py` | dead code (import module không tồn tại, Groq không dùng) | — | — |
| `app/rag/*`, `scripts/sync_data.py` | FAISS cục bộ chưa từng chạy; script select cột không tồn tại và ghi DB bằng secret key | knowledge qua Backend (GAP B.7) | |
| `crawler/crawl_sitemap.py`, `data/product_urls.csv` | không thuộc agent; thu thập sitemap website bên thứ ba | — | cần xác nhận quyền sử dụng nếu khôi phục |
| `test_system_suite.py`, `test_normalization_suite.py` | script in PASS/FAIL, không assert; ca kiểm thử chuẩn hóa đã chuyển sang `tests/test_planner.py` | `tests/` | |
| `desktop.ini` | file hệ thống Windows bị commit | — | — |

### Deprecated
- `POST /chat` — dùng `/v1/agent/chat`. Giữ cho Frontend hiện tại và luồng Backend `/api/ai-chat/...` (BLOCKER B-2).
- Thao tác giỏ hàng trong chatbot — Backend không có API giỏ hàng; agent hướng người dùng dùng website.

### Security
- Bỏ Supabase secret key khỏi runtime; mọi truy cập dữ liệu qua Backend bằng JWT của người dùng.
- Permission kiểm tra trước validation; mutation luôn cần xác nhận gắn action + mã; audit mọi mutation và từ chối.
- Allowlist endpoint Backend; không có tool tùy ý; grounding guard cho câu trả lời LLM.
