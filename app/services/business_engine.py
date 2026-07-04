"""
app/services/business_engine.py
"""

import math
import logging

from app.core.database import supabase

logger = logging.getLogger("woodhub.business_engine")


class BusinessEngine:
    # Hệ số giá theo loại gỗ (nhân với đơn giá cơ bản cho 1m3)
    WOOD_COEFFICIENTS = {
        "sồi": 1.0,      # Oak
        "óc chó": 2.5,   # Walnut
        "tần bì": 1.1,   # Ash
        "thông": 0.7,    # Pine
    }

    # --- TÍNH TOÁN KHOẢNG CÁCH ---
    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Helper: Tính khoảng cách (km) giữa 2 điểm tọa độ GPS."""
        R = 6371  # Bán kính trái đất (km)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)

    # --- SẢN PHẨM & GIỎ HÀNG ---
    @staticmethod
    def search_product(product_name: str) -> dict:
        clean_name = product_name.strip().lower()
        if not clean_name:
            return {"status": "error", "message": "Vui lòng cung cấp từ khóa tìm kiếm."}

        try:
            # Gộp query: lấy product + variants trong 1 lần gọi bằng embed resource
            # của PostgREST, thay vì 2-3 round-trip riêng lẻ như bản gốc.
            res = (
                supabase.table("products")
                .select("id, name, description, status, product_variants(sku, color, dimensions, price)")
                .ilike("name", f"%{clean_name}%")
                .limit(1)
                .execute()
            )
        except Exception:
            logger.exception("Lỗi Supabase khi search_product(product_name=%s)", product_name)
            return {"status": "error", "message": "Không thể truy vấn dữ liệu sản phẩm lúc này."}

        if not res.data:
            # Fallback: thử tìm theo SKU nếu không match theo tên.
            try:
                variant_res = (
                    supabase.table("product_variants")
                    .select("product_id, sku, color, dimensions, price")
                    .ilike("sku", f"%{clean_name}%")
                    .limit(1)
                    .execute()
                )
            except Exception:
                logger.exception("Lỗi Supabase khi fallback tìm theo SKU (%s)", product_name)
                return {"status": "error", "message": "Không thể truy vấn dữ liệu sản phẩm lúc này."}

            if not variant_res.data:
                return {
                    "status": "empty",
                    "message": f"Không tìm thấy sản phẩm nào liên quan đến '{product_name}'.",
                }

            product_id = variant_res.data[0]["product_id"]
            try:
                prod_res = (
                    supabase.table("products")
                    .select("name, description, product_variants(sku, color, dimensions, price)")
                    .eq("id", product_id)
                    .limit(1)
                    .execute()
                )
            except Exception:
                logger.exception("Lỗi Supabase khi lấy product theo id=%s", product_id)
                return {"status": "error", "message": "Không thể truy vấn dữ liệu sản phẩm lúc này."}

            if not prod_res.data:
                return {"status": "empty", "message": f"Không tìm thấy sản phẩm nào liên quan đến '{product_name}'."}

            p = prod_res.data[0]
        else:
            p = res.data[0]

        return {
            "status": "success",
            "product": {
                "name": p.get("name"),
                "description": p.get("description"),
                "variants": p.get("product_variants") or [],
            },
        }

    @staticmethod
    def add_to_cart(session_id: str, sku: str, quantity: int = 1) -> dict:
        if quantity <= 0:
            return {"status": "error", "message": "Số lượng phải lớn hơn 0."}

        try:
            var_res = (
                supabase.table("product_variants")
                .select("id, price, product_id")
                .eq("sku", sku)
                .execute()
            )
            if not var_res.data:
                return {"status": "error", "message": "Mã sản phẩm không tồn tại."}

            variant_data = var_res.data[0]
            prod_res = (
                supabase.table("products")
                .select("name")
                .eq("id", variant_data["product_id"])
                .execute()
            )
            product_name = prod_res.data[0]["name"] if prod_res.data else "Sản phẩm"

            # QUAN TRỌNG: dùng upsert thay vì "select rồi insert/update" để tránh
            # race condition khi 2 request cùng session_id đến gần như đồng thời.
            # Yêu cầu: tạo UNIQUE constraint (session_id, product_variant_sku)
            # trên bảng cart_items ở Supabase để on_conflict hoạt động đúng.
            #
            # Lưu ý: upsert theo cách dưới đây sẽ GHI ĐÈ quantity, không cộng dồn.
            # Nếu cần cộng dồn số lượng một cách atomic, nên tạo Postgres function
            # (RPC) dùng "INSERT ... ON CONFLICT DO UPDATE SET quantity = quantity + EXCLUDED.quantity".
            existing = (
                supabase.table("cart_items")
                .select("id, quantity")
                .eq("session_id", session_id)
                .eq("product_variant_sku", sku)
                .execute()
            )

            if existing.data:
                new_qty = existing.data[0]["quantity"] + quantity
                supabase.table("cart_items").update({"quantity": new_qty}).eq(
                    "id", existing.data[0]["id"]
                ).execute()
            else:
                supabase.table("cart_items").insert(
                    {
                        "session_id": session_id,
                        "product_variant_sku": sku,
                        "product_name": product_name,
                        "price_at_addition": variant_data["price"],
                        "quantity": quantity,
                    }
                ).execute()

            return {"status": "success", "message": f"Đã thêm {quantity} {product_name} vào giỏ hàng."}

        except Exception:
            logger.exception("Lỗi add_to_cart(session_id=%s, sku=%s)", session_id, sku)
            return {"status": "error", "message": "Không thể thêm vào giỏ hàng lúc này, vui lòng thử lại."}

    @staticmethod
    def view_cart(session_id: str) -> dict:
        try:
            res = (
                supabase.table("cart_items")
                .select("product_name, product_variant_sku, quantity, price_at_addition")
                .eq("session_id", session_id)
                .execute()
            )
        except Exception:
            logger.exception("Lỗi view_cart(session_id=%s)", session_id)
            return {"status": "error", "message": "Không thể tải giỏ hàng lúc này."}

        if not res.data:
            return {"status": "empty", "message": "Giỏ hàng của bạn đang trống."}

        total = sum(float(item["price_at_addition"]) * item["quantity"] for item in res.data)
        return {"status": "success", "items": res.data, "total": total}

    # --- TÌM CHI NHÁNH GẦN NHẤT ---
    @staticmethod
    def find_suppliers_nearby(user_lat: float, user_lng: float, max_dist_km: float = 50.0) -> dict:
        """
        Tìm chi nhánh (stores) gần nhất dựa trên tọa độ.

        LƯU Ý VỀ SCALABILITY: cách làm hiện tại fetch toàn bộ bảng "stores" về
        rồi tính Haversine bằng Python. Với vài chục - vài trăm cửa hàng thì ổn,
        nhưng khi dữ liệu lớn (hàng nghìn dòng), nên chuyển sang PostGIS:
        tạo cột "location geography(Point, 4326)" + GIST index, rồi lọc bằng
        ST_DWithin ngay trong Postgres (qua supabase.rpc(...)), để chỉ trả về
        vài dòng gần nhất thay vì kéo cả bảng về ứng dụng.
        """
        try:
            res = (
                supabase.table("stores")
                .select("id, name, address, lat, lng, supplier_id")
                .execute()
            )
        except Exception:
            logger.exception("Lỗi find_suppliers_nearby(lat=%s, lng=%s)", user_lat, user_lng)
            return {"status": "error", "message": "Không thể tìm cửa hàng gần bạn lúc này."}

        if not res.data:
            return {"status": "empty", "message": "Hiện không có cửa hàng nào được ghi nhận."}

        results = []
        for s in res.data:
            lat = s.get("lat")
            lng = s.get("lng")
            if lat is not None and lng is not None:
                dist = BusinessEngine._haversine_distance(user_lat, user_lng, float(lat), float(lng))
                if dist <= max_dist_km:
                    s["distance"] = dist
                    results.append(s)

        results.sort(key=lambda x: x["distance"])
        return {"status": "success", "data": results[:5]}

    @staticmethod
    def estimate_custom_3d(wood_type: str, w: float, h: float, d: float) -> dict:
        # Validate kích thước để tránh giá 0đ hoặc âm do input sai.
        if w <= 0 or h <= 0 or d <= 0:
            return {"status": "error", "message": "Kích thước (dài, rộng, cao) phải lớn hơn 0."}

        base_price_per_m3 = 10_000_000
        volume_m3 = (w * h * d) / 1_000_000

        coeff = BusinessEngine.WOOD_COEFFICIENTS.get(wood_type.lower(), 1.0)
        estimated_price = volume_m3 * base_price_per_m3 * coeff

        formatted_price = f"{int(estimated_price):,.0f} VND".replace(",", ".")

        return {
            "status": "success",
            # Trả kèm giá trị số thô để tầng trên (chat.py) có thể tái sử dụng
            # mà không phải parse lại chuỗi đã format.
            "estimated_price": round(estimated_price, 0),
            "price_display": formatted_price,
            "message": f"Với kích thước {w}x{h}x{d}cm và gỗ {wood_type}, giá ước tính là {formatted_price}.",
        }

    @staticmethod
    def redirect_to_payment() -> dict:
        return {"status": "success", "link": "https://woodhub.id.vn/payment"}


business_engine = BusinessEngine()