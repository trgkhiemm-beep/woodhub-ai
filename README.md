# WoodHub AI Agent

AI Store Knowledge & Management Agent cho WoodHub: hiểu tiếng Việt tự nhiên (không dấu, teencode, viết tắt, Anh-Việt,
nhiều ý), nhớ ngữ cảnh hội thoại ("mẫu 2", "cái này", "rẻ hơn", "nhỏ hơn"), hỏi lại khi thiếu thông tin, tư vấn sản phẩm
trên dữ liệu thật và thực hiện thay đổi dữ liệu **có kiểm soát** (phân quyền → xác nhận → thực thi → xác minh → audit).
WoodHub Backend (`https://woodhub-be.onrender.com`) là source of truth; agent không truy cập database.

- Kiến trúc, bảo mật, kết quả đánh giá: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Contract Frontend: [docs/FRONTEND_INTEGRATION.md](docs/FRONTEND_INTEGRATION.md) · OpenAPI: [contracts/agent-api.openapi.json](contracts/agent-api.openapi.json)
- Contract Backend: [docs/BACKEND_INTEGRATION.md](docs/BACKEND_INTEGRATION.md)

## Chạy local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Linux/macOS: .venv/bin/pip
cp .env.example .env                                   # BACKEND_BASE_URL bắt buộc; AWS + BEDROCK_MODEL_ID để bật LLM
.venv/Scripts/uvicorn app.main:app --reload --port 8000
```
Mở `http://localhost:8000/docs`. Ví dụ `POST /v1/agent/chat`:

| Tin nhắn | Agent làm gì |
|---|---|
| `mk can ban an 6 ng tam 10 cu` | `- Tên — giá` các bàn ăn đủ 6 chỗ ≤ 10 triệu trên catalog thật (lọc chặt) |
| `Có mẫu nhỏ hơn không?` (lượt sau) | đọc lại mẫu vừa gợi ý, tìm mẫu có diện tích nhỏ hơn |
| `mau 2 con ko` | tồn kho của mẫu thứ 2 trong danh sách vừa hiển thị |
| `giá KTV01 và bảo hành bao lâu` | `- Kệ Tivi Gỗ KTV01 — 2.737.000đ` + "chưa có thông tin đã xác minh" về bảo hành |
| `thời tiết hôm nay thế nào` | `Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ của cửa hàng.` (không gọi LLM) |
| `có bàn ăn nào dưới 500 nghìn không` | `Hiện hệ thống chưa cập nhật sản phẩm phù hợp.` |
| `Tư vấn cho tôi một cái bàn` | hỏi lại ngân sách/kích thước (một lần) |
| (supplier, `/v1/agent/manage/chat`) `Đổi giá KTV01 thành 3 triệu` | hiển thị giá cũ → mới, chờ `xác nhận <MÃ>` |

## NLU

`NLU_MODE=auto` (mặc định): domain guard + bộ luật deterministic chạy trước; chỉ câu bộ luật không chắc chắn mới gọi LLM
(`BEDROCK_MODEL_ID` + AWS key) để phân loại ý/tách câu (~4–8% lượt). Mọi giá trị (mã sản phẩm, tiền, số người, tham chiếu…)
do code trích xuất; lệnh thay đổi dữ liệu và xác nhận không bao giờ đi qua LLM. LLM lỗi → bộ luật. Đã kiểm thử với `google.gemma-3-4b-it` (xem ARCHITECTURE §3).

## Test & đánh giá

```bash
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m tests.eval.run --mode llm --suite holdout2
```
- Unit test không cần mạng. Test live dùng **dữ liệu thật** (Backend + đối chiếu Supabase, cần `SUPABASE_URL`/`SUPABASE_KEY`
  trong `.env`, chỉ SELECT); tự skip khi không kết nối được. Không request ghi nào tới production (transport chỉ-đọc + chặn ghi).
- `tests/test_hardening.py`: domain guard, chuẩn hóa giá, câu trả lời ngắn, sự thật sản phẩm (đối chiếu Supabase), lỗi Backend,
  chống bịa sản phẩm/vượt nguồn dữ liệu.
- Agent Evaluation: 150 câu thực tế (`tests/eval/*.json`), 11 chỉ số + chi phí (LLM call, token, truy vấn Backend);
  `test_agent_eval.py` khóa ngưỡng ở chế độ rules;
  chế độ LLM chạy bằng lệnh trên (tốn vài trăm lượt gọi Bedrock).

## Deploy
- `Dockerfile` hoặc `pip install -r requirements.txt`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Env bắt buộc: `APP_ENV=production`, `BACKEND_BASE_URL`, `CORS_ORIGINS`, `BACKEND_JWT_SECRET` (= `JWT_SECRET` của Backend,
  để verify JWT tại chỗ). Tùy chọn LLM: `AWS_*`, `BEDROCK_MODEL_ID`.
- Không cần `SUPABASE_*` ở runtime. Chạy **1 instance** (state xác nhận/session in-memory).
