# WoodHub AI Agent

AI Store Knowledge & Management Agent cho WoodHub: trả lời về sản phẩm, giá, tồn kho, cửa hàng, khuyến mãi, chính sách và
thực hiện thay đổi dữ liệu **có kiểm soát** (phân quyền → xác nhận → thực thi → xác minh → audit).
WoodHub Backend (`https://woodhub-be.onrender.com`) là source of truth; agent không truy cập database trực tiếp.

- Kiến trúc: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Contract Frontend: [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) · OpenAPI: [contracts/agent-api.openapi.json](contracts/agent-api.openapi.json)
- Contract Backend: [docs/BACKEND_INTEGRATION_SPEC.md](docs/BACKEND_INTEGRATION_SPEC.md)
- Bảo mật: [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) · Thay đổi: [docs/CHANGELOG.md](docs/CHANGELOG.md)

## Chạy local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Linux/macOS: .venv/bin/pip
cp .env.example .env                                   # điền BACKEND_BASE_URL (bắt buộc)
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```
Mở `http://localhost:8000/docs`. Ví dụ: `POST /v1/agent/chat {"message": "Giá Bàn Ăn TB06"}`.

## Test

```bash
.venv/Scripts/python -m pytest -q
```
- Unit test không cần mạng.
- Test live dùng **dữ liệu thật**: Backend thật + đối chiếu Supabase (chỉ SELECT, cần `SUPABASE_URL`/`SUPABASE_KEY` trong `.env`).
  Tự skip nếu không kết nối được. Không có request ghi nào tới production (transport chỉ-đọc + chặn ghi).

## Deploy (Render hoặc bất kỳ container host)
- Build: `Dockerfile` (hoặc `pip install -r requirements.txt`).
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Env bắt buộc: `APP_ENV=production`, `BACKEND_BASE_URL`, `CORS_ORIGINS` (domain Frontend). Tùy chọn: `AGENT_PLANNER=llm` + `BEDROCK_MODEL_ID` + AWS key.
- **Không** cần `SUPABASE_KEY` ở runtime — hãy xóa khỏi môi trường production.
- Chạy **1 instance** (state xác nhận/session in-memory — xem ARCHITECTURE §9).
