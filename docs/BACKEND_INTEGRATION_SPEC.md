# BACKEND_INTEGRATION_SPEC — WoodHub AI Service ↔ Backend (rev 4 — implemented adapters)

> **Rev 3 (2026-09-30):** đối chiếu với OpenAPI thật của Backend tại `https://woodhub-be.onrender.com/v3/api-docs` (Swagger UI: `/swagger-ui/index.html`), 156 operation, và một số GET ẩn danh (không ghi dữ liệu).
>
> Tài liệu gồm 2 phần tách bạch:
> - **Phần A — API HIỆN CÓ** [VERIFIED từ OpenAPI / gọi thử]: AI service sẽ dùng lại, **không đề xuất làm lại**.
> - **Phần B — PROPOSED CONTRACT** cho các khoảng trống (GAP). ⚠️ Đây **không phải** API hiện tại; cần team Backend đồng ý.
>
> Lưu ý: OpenAPI chỉ khai báo response `200` và một security scheme toàn cục (`bearerAuth`) cho mọi operation; endpoint nào công khai chỉ suy ra được từ `summary` hoặc gọi thử. Mã lỗi, giới hạn, idempotency **không được tài liệu hóa**.

---

## PHẦN 0 — ĐÃ IMPLEMENT TRONG AI SERVICE (2026-09-30)

Adapter: `app/adapters/backend/` · Allowlist: `app/adapters/backend/client.py::ALLOWED_ENDPOINTS` · Port: `app/ports.py`.

| Port method | Backend endpoint (thật) | Auth gửi đi | Retry | Kiểm chứng |
|---|---|---|---|---|
| `IdentityPort.resolve` | `GET /api/users/me` | Bearer token người dùng | GET ×2 | live: token giả → 401 |
| `CatalogPort.search_products` | `GET /api/products?keyword&categoryId&materialId&minPrice&maxPrice&room&style&available&page&size` | token nếu có | GET ×2 | live + đối chiếu Supabase |
| `CatalogPort.get_product` | `GET /api/products/{id}` | token nếu có | GET ×2 | live + Supabase |
| `CatalogPort.find_product_by_sku` | keyword search + quét ≤ `SKU_SCAN_MAX_PRODUCTS` chi tiết | | | live (GAP B.2) |
| `list_categories/materials/rooms/styles` | `GET /api/categories|materials|rooms|styles` | | GET ×2 | live + Supabase |
| `update_product_description` | `PUT /api/products/{id}` `{name, description, categoryId, materialId}` (gửi lại giá trị hiện tại vì name/categoryId bắt buộc) | token supplier | PUT ×2 | shape theo OpenAPI; **chưa ghi production** |
| `update_variant_price` | `PUT /api/variants/{id}` `{price, sku, color, dimensions}` | token supplier | PUT ×2 | như trên |
| `upsert_category` / `upsert_material` | `POST/PUT /api/categories`, `/api/materials` | token admin | POST: không | như trên |
| `InventoryPort.get_inventory` | `GET /api/variants/{id}/inventory` (chỉ khi role=supplier) | token supplier | GET ×2 | khách → UNKNOWN |
| `InventoryPort.adjust_inventory` | `PATCH /api/stores/{storeId}/inventory/{variantId}` `{delta}` | token supplier | **không** | chưa ghi production |
| `StorePort.list_branches` | `GET /api/suppliers/public?type=retailer` + `GET /api/suppliers/{id}/stores` | | GET ×2 | live + Supabase |
| `StorePort.find_nearby_workshops` | `GET /api/stores/nearby/workshops?lat&lng&limit` | token | GET ×2 | guest → cần đăng nhập |
| `DesignPort.get_design_task` | `GET /api/custom/ai/tasks/{id}` | token | GET ×2 | |
| `StorePort.get_store_info/update_store_info`, `PromotionPort.*`, `KnowledgePort.*` | **GAP** → `CapabilityUnavailable` | — | — | live: trả "chưa có thông tin đã xác minh" |

Khi Backend làm xong một GAP ở Phần B: chỉ sửa method tương ứng trong `adapters.py` + thêm dòng allowlist; agent core, tools, contract Frontend không đổi.

### Việc team Backend cần làm (ưu tiên)
1. **B-2** — chuyển luồng `/api/ai-chat/sessions/{id}/messages` sang gọi `POST {AI}/v1/agent/chat` (hoặc `/v1/agent/manage/chat` cho admin/supplier), **forward header `Authorization`** của người dùng, đọc `message` + `blocks` (map `product_list`/`product_detail` → `suggestedProducts`). `/chat` cũ vẫn chạy trong giai đoạn chuyển tiếp.
2. **B-1** — xác nhận `GET /api/users/me` là cách verify token được chấp nhận (hoặc cung cấp public key/JWKS để bỏ 1 request/lượt).
3. **B-3** — trả **401** cho token thiếu/hết hạn (hiện 403) và đánh dấu endpoint công khai trong OpenAPI.
4. GAP B.1 (platform info), B.6 (promotion), B.7 (policy/FAQ/knowledge search), B.3 (tồn kho công khai), B.9 (audit API), B.2 (tra SKU); bổ sung SKU cho 26/27 biến thể đang `NULL`.
5. Cấp tài khoản **test/staging** (supplier + admin) để chạy kiểm thử ghi thật; hiện luồng ghi mới được kiểm chứng tới bước gửi request.
6. Rotate/giới hạn Supabase secret key vì AI service không còn cần (SECURITY S1).

---

## PHẦN A — BACKEND THỰC TẾ

### A.1 Tổng quan [VERIFIED]

| Hạng mục | Thực tế |
|---|---|
| Stack | Spring Boot ("Spring Boot JWT API" v1.0), OpenAPI 3.0.1, deploy Render |
| Base URL | `https://woodhub-be.onrender.com` (một môi trường; chưa rõ dev/staging) |
| Prefix | `/api/...` (không version) |
| Auth | `Authorization: Bearer <JWT>`; `POST /api/auth/login` · `/google` · `/refresh` · `/logout` → `AuthResponse{token, refreshToken, tokenType, userId, email, fullName, role: customer|admin|supplier, customerType, supplierType, mustChangePassword}` |
| Danh tính hiện tại | `GET /api/users/me` → `UserResponse{id, email, fullName, phone, role, …}` |
| Chưa đăng nhập | trả **403** (không phải 401) — ví dụ `/api/users/me`, `/api/ai-chat/sessions` |
| Lỗi | mặc định Spring: `{"timestamp","status","error","path"}` — không có `code`, không có message nghiệp vụ trong ví dụ đã gọi |
| Phân trang | `?page=0&size=20&sort=createdAt,DESC` (0-based) → `{"content":[…], "page":{"size","number","totalElements","totalPages"}}` |
| Tiền | `number` thập phân (vd `30000.00`) |
| Idempotency / ETag | không có trong tài liệu (chỉ `CustomDesign` có `version` optimistic lock) |

### A.2 Ánh xạ năng lực Agent → API Backend

Trạng thái: **EXISTS** dùng được ngay · **PARTIAL** có nhưng thiếu · **GAP** chưa có (→ Phần B).

| Năng lực agent (tool) | Trạng thái | Endpoint Backend | Ghi chú |
|---|---|---|---|
| Danh tính, role | EXISTS | `GET /api/users/me` | dùng để verify token nếu không có public key (BLOCKER B-1) |
| Tìm sản phẩm (`search_products`) | EXISTS | `GET /api/products?keyword&categoryId&materialId&minPrice&maxPrice&room&style&available&has3d&customizable&page&size&sort` — công khai | → `ProductSummaryResponse{id, name, supplierName, categoryName, materialName, priceFrom, primaryImageUrl, status}`. **`keyword` chỉ khớp có dấu**: "bàn" → 14, "ban" → 0 [VERIFIED] → AI phải khôi phục dấu trước khi gọi (tận dụng `input_normalizer`). |
| Chi tiết sản phẩm (`get_product`) | EXISTS | `GET /api/products/{id}` — công khai | variants (sku, color, dimensions, price), images. **Không** có tồn kho, giá khuyến mãi, model 3D. |
| Tra theo SKU | GAP | — | B.2 |
| Tồn kho (`get_availability`) | PARTIAL | `GET /api/variants/{id}/inventory` — **chỉ supplier chủ**; filter `available=true` trên `/api/products` | `store_inventory` đang 0 dòng → `available=true` trả 0 [VERIFIED]. Cần endpoint công khai (B.3). |
| Danh mục, vật liệu | EXISTS | `GET /api/categories`, `/api/categories/tree`, `/api/materials` — công khai | |
| Phòng, phong cách, phòng mẫu | EXISTS | `GET /api/rooms`, `/api/rooms/{slug}`, `/api/rooms/{slug}/scenes`, `/api/room-scenes/{id}`, `/api/styles` — công khai | |
| Model 3D mẫu | EXISTS | `GET /api/custom/models`, `/api/custom/models/{slug}` | |
| Đánh giá | EXISTS | `GET /api/reviews`, `/api/reviews/summary` | |
| Nhà cung cấp công khai | EXISTS | `GET /api/suppliers/public?type`, `/api/suppliers/{id}/public` (contactEmail, contactPhone), `/api/suppliers/{id}/stores` (chỉ quận/thành phố) | |
| Cửa hàng gần khách (`find_nearby_stores`) | PARTIAL | `GET /api/stores/nearby/by-supplier/{supplierId}?lat&lng` — cần đăng nhập, **theo từng supplier** | Chưa có "mọi cửa hàng retailer gần tôi" cho guest (B.4). |
| Xưởng gần khách (`match_workshops`) | EXISTS | `GET /api/stores/nearby/workshops?lat&lng&limit`, `/radius?radiusKm` — cần đăng nhập | Đã dùng trong tool `find_nearby_workshops` (dữ liệu mock cũ đã xóa). Không có trường `capabilities` → bỏ chấm điểm theo năng lực hoặc Backend bổ sung. |
| Báo giá đặt làm (`estimate_custom_price`) | PARTIAL | Luồng thật: `POST /api/custom/designs` → `POST /api/quotes` (xưởng ra giá, đàm phán) | Backend **không** có công thức ước tính giá; con số 10.000.000 đ/m³ chỉ có trong AI service → B.5. |
| Dựng 3D từ ảnh (`start_design_3d_from_image`) | EXISTS | `POST /api/custom/ai/generate` (multipart, trừ quota `design`, 429 khi hết) · `GET /api/custom/ai/tasks/{taskId}` (owner/admin) · `/my` · `/retry` · `/cancel` | **Backend đã tích hợp tạo 3D** → đã gỡ Meshy khỏi AI service. |
| Quota | EXISTS | `GET /api/usage/me`, `POST /api/usage/{feature}/consume` | |
| Lịch sử chat AI | EXISTS | `GET/POST /api/ai-chat/sessions`, `GET /api/ai-chat/sessions/{id}/messages` | |
| Gửi tin nhắn AI | EXISTS (proxy) | `POST /api/ai-chat/sessions/{id}/messages` `{content, lat?, lng?}` → `AiChatMessageResponse{id, role, content, extractedParams, suggestedProducts[], createdAt}`; trừ quota `ai_chat` | **Backend đang đứng giữa Frontend và AI service.** Cách Backend gọi AI service chưa được tài liệu hóa (BLOCKER B-2). DB: 14 cặp tin nhắn, lần cuối 2026-07-20; `suggestedProducts` có các key `id, name, description, price, status`. |
| Giỏ hàng (`view_cart`, `add_to_cart`) | **GAP** | Không có endpoint cart nào | Bảng `carts`/`cart_items` tồn tại nhưng không có API; Backend chỉ có thanh toán gói (`/api/payments/subscription`), không có đơn hàng bán lẻ. → BLOCKER B-4. |
| Thông tin platform (hotline, giờ mở cửa) | GAP | — | B.1 |
| Promotion / voucher | GAP | — | B.6 |
| Policy / FAQ / hướng dẫn / knowledge search | GAP | — | B.7 |
| Admin: danh mục, vật liệu | EXISTS | `POST/PUT/DELETE /api/categories…`, `/api/materials…` (admin) | có thể thành tool admin (ARCHITECTURE §5.3) |
| Admin: phòng, phong cách, phòng mẫu, gắn sản phẩm | EXISTS | `/api/admin/rooms`, `/api/admin/styles`, `/api/admin/room-scenes…`, `/api/admin/products/{id}/rooms|styles/{…}` | |
| Admin: nhà cung cấp | EXISTS | `GET /api/suppliers`, `GET /api/suppliers/{id}`, `PUT /api/suppliers/{id}/status` | đổi trạng thái supplier = SENSITIVE |
| Admin: quota user | EXISTS | `GET /api/usage/users/{id}`, `PUT /api/usage/users/{id}/{feature}/limit` | |
| Admin: ẩn/hiện sản phẩm (moderation) | GAP | `PATCH /api/products/{id}/status` chỉ cho **supplier chủ** | B.8 |
| Audit log | GAP | — | B.9 |
| Agent actions (propose/confirm) | GAP | — | B.10 (có phương án không cần Backend) |

### A.3 Endpoint Backend mà AI service **không bao giờ** gọi

Auth (login/register/OTP/reset), users CRUD, đổi mật khẩu, payments/webhook, subscriptions, DELETE bất kỳ, CRUD sản phẩm/biến thể/ảnh/tồn kho của supplier, chat customer↔supplier (`/api/conversations`). Allowlist được cấu hình tường minh trong `BackendClient`.

---

## BLOCKERS (đã thu hẹp sau khi có Swagger)

```text
BLOCKED — BACKEND INFORMATION REQUIRED

Need:
B-1. Cách service khác verify JWT: thuật toán, secret/public key hoặc JWKS, tên claim chứa userId/role, thời hạn.
     (Nếu không chia sẻ được key → AI service gọi GET /api/users/me để xác thực, tốn 1 request/lượt.)
B-2. Luồng POST /api/ai-chat/sessions/{id}/messages gọi AI service thế nào: URL nào (/chat?), gửi field gì,
     có chuyển tiếp JWT người dùng không, đọc SSE hay JSON, map extractedParams/suggestedProducts từ đâu,
     timeout bao nhiêu, có xác thực service-to-service không. (Code AI đã đổi nhiều sau 2026-07-20 —
     lần cuối có tin nhắn AI trong DB — nên tích hợp hiện tại có thể đã hỏng.)
B-3. Danh sách endpoint công khai chính xác (OpenAPI không đánh dấu) và mã lỗi nghiệp vụ.
B-4. Giỏ hàng/đơn hàng bán lẻ có nằm trong phạm vi Backend không? Nếu không → AI bỏ tính năng giỏ hàng.
B-5. Ai sở hữu công thức/tham số báo giá ước tính đồ đặt làm (hiện chỉ có trong code AI)?
B-6. Kế hoạch cho: platform info, promotion/voucher, policy/FAQ/knowledge, audit log, moderation của admin.
B-7. "Chi nhánh" của WoodHub: có showroom riêng hay chỉ là stores của supplier?

Why:
Phần A đã đủ để làm các tool READ/SEARCH về sản phẩm, danh mục, phòng, xưởng, 3D và lịch sử chat.
B-1, B-2 quyết định kiến trúc xác thực và cách hai service nối với nhau; B-4..B-6 quyết định phạm vi tính năng.

Where to get it:
Team Backend (repo Spring Boot: SecurityConfig/JwtUtil, service gọi AI trong module AiChat), cấu hình Render.
```

---

## PHẦN B — PROPOSED CONTRACT CHO CÁC GAP

> ⚠️ Đề xuất, không phải API hiện tại. Viết theo **quy ước sẵn có của Backend** (prefix `/api`, `PagedModel` 0-based, Bearer JWT) để dễ tích hợp.

### B.0 Quy ước bổ sung đề xuất
- Lỗi nghiệp vụ: giữ khung Spring nhưng thêm `code` và `message` (vd `{"timestamp","status":422,"error":"Unprocessable Entity","code":"VERSION_CONFLICT","message":"…","path"}`), và trả **401** cho token thiếu/hết hạn (hiện là 403) để client phân biệt với thiếu quyền.
- Mutation mới nhận header `Idempotency-Key`; tài nguyên sửa được trả `version`/`updatedAt` và nhận `expectedVersion`.
- `X-Request-Id` được log và trả lại.

### B.1 Platform info
`GET /api/platform/info` (công khai) · `PUT /api/admin/platform/info` (admin, `expectedVersion`)
```json
{"name":"WoodHub","hotline":"…","email":"…","address":"…",
 "openingHours":[{"days":["mon","tue","wed","thu","fri"],"open":"08:00","close":"21:00"}],
 "socialLinks":{"facebook":"…","zalo":"…"},"version":3,"updatedAt":"…"}
```

### B.2 Tra sản phẩm theo SKU
`GET /api/variants/by-sku/{sku}` (công khai) → `ProductVariantResponse` + `productId`.

### B.3 Tình trạng hàng công khai
`GET /api/variants/{variantId}/availability` (công khai) → `{"variantId","status":"in_stock|out_of_stock|unknown","totalStock"?,"asOf"}`. `unknown` khi không có bản ghi `store_inventory`. Có trả số lượng chính xác cho khách không → Backend/nghiệp vụ quyết định.

### B.4 Cửa hàng gần khách (mọi retailer)
`GET /api/stores/nearby?lat&lng&limit&supplierType=retailer` (công khai hoặc cần đăng nhập — Backend quyết định) → `NearbyStoreResponse[]` (đã có schema).

### B.5 Tham số báo giá ước tính
`GET /api/pricing/custom-furniture` (công khai)
```json
{"basePricePerM3":10000000,"woodCoefficients":{"sồi":1.0,"óc chó":2.5,"tần bì":1.1,"thông":0.7,"cao su":0.8},
 "minDimensionCm":1,"maxDimensionCm":400,"version":1,"disclaimer":"Giá ước tính…"}
```
Các số trên là **giá trị hiện hard-code trong AI service**, chưa được nghiệp vụ xác nhận. Nếu nghiệp vụ muốn chỉ dùng luồng báo giá thật của xưởng (`/api/quotes`), AI bỏ tính năng ước tính.

### B.6 Promotions
| Method | Path | Quyền |
|---|---|---|
| GET | `/api/promotions?status=active&productId&categoryId&code` | công khai (chỉ trường khách được xem) |
| GET | `/api/admin/promotions?status&from&to&page` · `/api/admin/promotions/{id}` | admin |
| POST | `/api/admin/promotions` | admin, Idempotency-Key |
| PUT | `/api/admin/promotions/{id}` | admin, `expectedVersion` |
| PATCH | `/api/admin/promotions/{id}/status` `{status: active|paused|ended}` | admin |

```json
{"id":"uuid","name":"Giảm 20% bàn ăn","type":"percentage|fixed_amount|free_shipping","value":20,"maxDiscount":2000000,
 "scope":{"categoryIds":["uuid"],"productIds":[]},"startsAt":"…","endsAt":"…",
 "conditions":{"minOrderValue":5000000,"usageLimit":500,"perUserLimit":1},"code":"BANAN20",
 "status":"draft|scheduled|active|paused|ended","version":1}
```
Giá khuyến mãi nên được phản ánh trong `ProductResponse.variants[].salePrice` để AI không tự tính.

### B.7 Knowledge (policy, FAQ, hướng dẫn app) + RAG
| Method | Path | Quyền |
|---|---|---|
| GET | `/api/knowledge/policies/{type}` — `shipping|return|warranty|payment|terms|privacy` | công khai |
| GET | `/api/knowledge/faqs?category&page` | công khai |
| POST | `/api/knowledge/search` `{queryEmbedding \| query, kinds[], topK≤5}` | công khai |
| GET/POST/PUT | `/api/admin/knowledge…` (+ archive, lịch sử version) | admin |
| PUT | `/api/internal/knowledge/documents/{id}/chunks` | service token (nếu AI embed — Option K1) |

Bảng gợi ý: `knowledge_documents(id, kind, title, status, content, version, effective_from…)`, `knowledge_chunks(document_id, document_version, chunk_index, text, embedding vector(N))`. Cột `product_variants.embedding` hiện là `vector(768)` (khớp `keepitreal/vietnamese-sbert`), 0/27 dòng có dữ liệu. Chọn model embedding sau khi đánh giá tiếng Việt (IMPLEMENTATION_PLAN CP5). Webhook `knowledge.document.published|archived` (HMAC) để AI reindex.

### B.8 Moderation sản phẩm cho admin
`PATCH /api/admin/products/{id}/visibility` `{status: active|hidden, reason*}` — admin; ghi audit. (Hiện `PATCH /api/products/{id}/status` chỉ cho supplier chủ.)

### B.9 Audit log
`GET /api/admin/audit-logs?entityType&entityId&actorId&from&to&page` (admin) · `POST /api/internal/audit-events` (service token).
```json
{"id":"uuid","actorId":"uuid","actorRole":"admin","channel":"ai_admin_agent|admin_ui","operation":"policy.update",
 "entityType":"policy","entityId":"return","before":{…},"after":{…},"reason":"…","conversationId":"uuid","requestId":"…","createdAt":"…"}
```
Append-only. Khi chưa có: AI service ghi audit có cấu trúc vào log riêng (tạm thời, không thay thế được audit của Backend).

### B.10 Agent actions (propose → confirm → execute)

**Phương án 1 — Backend giữ pending action (khuyến nghị dài hạn):** `POST /api/admin/agent-actions` (dry-run, trả diff + impact + `expiresAt`), `POST /api/admin/agent-actions/{id}/execute` `{confirmationPhrase}`, `POST …/cancel`, `GET …/{id}`. Backend kiểm tra người tạo = người xác nhận, TTL, version; thực hiện + ghi audit trong cùng transaction. Danh sách `operation` cố định (vd `category.create`, `material.update`, `room_scene.update`, `supplier.set_status`, `promotion.create`, `policy.update`, `faq.update`, `platform_info.update`, `product.set_visibility`).

**Phương án 2 — không cần Backend làm mới (dùng được ngay cho các endpoint admin đã có):** AI service tạo *pending action* dạng token ký HMAC (`op`, `target`, `payload`, `adminId`, `expiresAt`, `targetUpdatedAt`); Frontend gửi lại token khi bấm Xác nhận; AI service verify chữ ký + người xác nhận + hạn, đọc lại bản ghi để so `updatedAt`, rồi gọi **endpoint admin hiện có** bằng JWT của admin. Hạn chế: không atomic với audit, chống replay dựa trên TTL ngắn + `updatedAt`.

---

## Checklist cho team Backend
- [ ] Trả lời B-1 … B-7
- [ ] Xác nhận/điều chỉnh các GAP B.1–B.10 và thứ tự ưu tiên
- [ ] Tài liệu hóa trong OpenAPI: endpoint công khai (`security: []`), mã lỗi 400/401/403/404/409/429
- [ ] Hỗ trợ tìm kiếm không dấu cho `keyword` (hoặc xác nhận AI tự khôi phục dấu)
- [ ] Cấp cho AI service quyền hẹp thay cho Supabase secret key (SECURITY S1) — mục tiêu: AI không truy cập DB trực tiếp
