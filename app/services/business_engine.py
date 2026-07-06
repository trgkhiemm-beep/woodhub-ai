import math
import logging

from app.core.database import supabase

logger = logging.getLogger("woodhub.business_engine")

class BusinessEngine:
    WOOD_COEFFICIENTS = {
        "sồi": 1.0,      
        "óc chó": 2.5,   
        "tần bì": 1.1,   
        "thông": 0.7,    
    }

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371  
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)

    @staticmethod
    def search_product(product_name: str) -> dict:
        clean_name = product_name.strip().lower()
        if not clean_name:
            return {"status": "error", "message": "Vui lòng cung cấp từ khóa tìm kiếm."}

        try:
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

    @staticmethod
    def find_suppliers_nearby(user_lat: float, user_lng: float, max_dist_km: float = 50.0) -> dict:
        try:
            # Đã sửa "name" thành "store_name" để fix lỗi code: 42703
            # Lưu ý: Hãy kiểm tra bảng stores trên Supabase, nếu bạn đặt là "title" hoặc tên khác thì sửa lại chữ "store_name" cho khớp nhé.
            res = (
                supabase.table("stores")
                .select("id, store_name, address, lat, lng, supplier_id")
                .execute()
            )
        except Exception as e:
            logger.exception("Lỗi find_suppliers_nearby(lat=%s, lng=%s) - Lỗi chi tiết: %s", user_lat, user_lng, e)
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
        if w <= 0 or h <= 0 or d <= 0:
            return {"status": "error", "message": "Kích thước (dài, rộng, cao) phải lớn hơn 0."}

        base_price_per_m3 = 10_000_000
        volume_m3 = (w * h * d) / 1_000_000

        coeff = BusinessEngine.WOOD_COEFFICIENTS.get(wood_type.lower(), 1.0)
        estimated_price = volume_m3 * base_price_per_m3 * coeff

        formatted_price = f"{int(estimated_price):,.0f} VND".replace(",", ".")

        return {
            "status": "success",
            "estimated_price": round(estimated_price, 0),
            "price_display": formatted_price,
            "message": f"Với kích thước {w}x{h}x{d}cm và gỗ {wood_type}, giá ước tính là {formatted_price}.",
        }

    @staticmethod
    def redirect_to_payment() -> dict:
        return {"status": "success", "link": "https://woodhub.id.vn/payment"}


business_engine = BusinessEngine()