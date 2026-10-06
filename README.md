# WoodHub AI Agent

Trợ lý AI **cho khách hàng** của sàn nội thất WoodHub (nhiều nhà cung cấp), **chỉ đọc** dữ liệu thật: tìm, xem giá/chi tiết,
so sánh (kể cả giữa các nhà cung cấp), tư vấn sản phẩm, thông tin liên hệ của nhà cung cấp, trạng thái đơn đặt làm, task 3D.
Hiểu tiếng Việt tự nhiên (không dấu, typo, teencode, viết tắt, Anh-Việt, nhiều ý), nhớ ngữ cảnh ("mẫu 2", "cái này", "shop này",
"rẻ hơn"), hỏi lại khi thiếu thông tin. **Không thay đổi dữ liệu** — Admin/Supplier cập nhật qua Backend Admin API.
WoodHub Backend (`https://woodhub-be.onrender.com`) là source of truth; agent không truy cập database.

- Kiến trúc, bảo mật, kết quả đánh giá: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Contract Frontend/App: [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) · OpenAPI: [contracts/agent-api.openapi.json](contracts/agent-api.openapi.json)
- Contract Backend: [docs/BACKEND_INTEGRATION.md](docs/BACKEND_INTEGRATION.md)

| Thành phần | Trách nhiệm |
|---|---|
| **WoodHub Backend** | AUTH + USER ACCESS CONTROL: đăng nhập/đăng ký, phát hành & kiểm tra JWT, role/permission, quyết định ai được gọi chat (khách `/api/ai-chat`, admin `/api/admin/ai-agent`) và ai được xem dữ liệu riêng |
| **AI Agent** | NATURAL LANGUAGE + RETRIEVAL + RECOMMENDATION + ORCHESTRATION — **không** xác thực người dùng, **không** đọc JWT, **không** phân quyền admin/supplier/customer, **không** tự chặn người chưa đăng nhập |
| **Supabase** | SOURCE OF TRUTH (Agent chỉ đọc qua Backend API) |

## Chạy local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Linux/macOS: .venv/bin/pip
cp .env.example .env                                   # BACKEND_BASE_URL bắt buộc; AWS + BEDROCK_MODEL_ID để bật LLM
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```
Mở `http://localhost:8000/docs`. Ví dụ `POST /v1/agent/chat`:

| Tin nhắn | Agent trả lời |
|---|---|
| `Tìm 3 bàn học dưới 2tr5` | `- Tên — giá` các bàn học ≤ 2.500.000đ trên catalog thật (lọc chặt) |
| `mk can ban an 6 ng tam 10 cu` | bàn ăn đủ 6 chỗ ≤ 10 triệu |
| `Giá KTV01` → `Shop này ở đâu?` | giá thật, rồi tên/điện thoại/khu vực của **nhà cung cấp** sản phẩm đó |
| `Cho tôi 3 bàn dưới 5 triệu từ các nhà cung cấp khác nhau` | mỗi nhà cung cấp tối đa 1 mẫu, kèm tên nhà cung cấp |
| `mau 2 con ko` | tình trạng hàng của mẫu thứ 2 vừa hiển thị |
| `chính sách đổi trả của shop này` | "chưa có thông tin đã xác minh" + liên hệ thật của nhà cung cấp |
| `thời tiết hôm nay thế nào` | `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ trên WoodHub.` (không gọi LLM) |
| `có bàn ăn nào dưới 500 nghìn không` | `Hiện hệ thống chưa cập nhật thông tin phù hợp.` |
| `Đổi giá KTV01 thành 3 triệu` | `Trợ lý AI chỉ hỗ trợ tra cứu và tư vấn, không thay đổi dữ liệu…` |

## NLU

`NLU_MODE=auto` (mặc định): nhận diện yêu cầu thay đổi + domain guard + bộ luật deterministic chạy trước; chỉ câu bộ luật không
chắc chắn mới gọi LLM (`BEDROCK_MODEL_ID` + AWS key, ~3–8% lượt) để phân loại ý/tách câu. Mọi giá trị (mã sản phẩm, tiền,
số người, tham chiếu, tên nhà cung cấp…) do code trích xuất. LLM lỗi → bộ luật. Đã kiểm thử với `google.gemma-3-4b-it`.

## Test & đánh giá

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m tests.eval.run --mode llm --suite holdout2
```
- Unit test không cần mạng. Test live dùng **dữ liệu thật** (Backend + đối chiếu Supabase, cần `SUPABASE_URL`/`SUPABASE_KEY`
  trong `.env`, chỉ SELECT); tự skip khi không kết nối được. Transport chỉ-đọc chặn mọi request không phải GET.
- `tests/test_customer_agent.py`: nhà cung cấp theo ngữ cảnh, đa nhà cung cấp, tồn kho, đơn hàng, từ chối thay đổi dữ liệu.
- `tests/test_hardening.py`: domain guard, chuẩn hóa giá, câu trả lời ngắn, sự thật sản phẩm, lỗi Backend, chống bịa.
- `tests/test_contract.py`: `contracts/agent-api.openapi.json` khớp code; alias camelCase; schema block.
- Agent Evaluation: 150 câu (`tests/eval/*.json`), 11 chỉ số + chi phí; `test_agent_eval.py` khóa ngưỡng ở chế độ rules.

Sinh lại contract khi đổi `app/api/schemas.py`:
```bash
.venv/Scripts/python -c "import json; from app.main import create_app; from tests.conftest import make_settings; json.dump(create_app(make_settings()).openapi(), open('contracts/agent-api.openapi.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

## Deploy
- `Dockerfile` hoặc `pip install -r requirements.txt`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Env bắt buộc: `APP_ENV`, `BACKEND_BASE_URL`, `CORS_ORIGINS`. Không cần secret JWT của người dùng.
  Tùy chọn: `AGENT_SERVICE_API_KEY` (khóa server-to-server Backend → Agent qua header `X-Agent-Api-Key`; khuyến nghị bật ở
  production), `AWS_*`, `BEDROCK_MODEL_ID` (LLM), `BACKEND_KEEPALIVE_SECONDS` (giữ Backend Render thức).
- Không cần `SUPABASE_*` ở runtime. Chạy **1 instance** (session/rate limit in-memory).
