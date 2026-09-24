import math
import logging
import re
from app.core.database import supabase
from app.services.input_normalizer import remove_vietnamese_diacritics, normalize_input

logger = logging.getLogger("woodhub.business_engine")

class BusinessEngine:
    WOOD_COEFFICIENTS = {
        "sồi": 1.0,      
        "óc chó": 2.5,   
        "tần bì": 1.1,   
        "thông": 0.7,    
    }

    def __init__(self):
        self.supabase = supabase
        if not self.supabase:
            logger.error("Hệ thống nghiêm trọng: Supabase Client chưa được nạp từ app.core.database!")

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6371  
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)

    def search_product(self, keyword: str = None, min_price: float = None, max_price: float = None, category: str = None, material: str = None, color: str = None) -> dict:
        """
        DATABASE-FIRST SEARCH SYSTEM.
        Truy vấn toàn bộ hoặc lọc sản phẩm từ Supabase với hỗ trợ Tiếng Việt không dấu & lọc chuẩn.
        
        Trả về dict:
        - status: "success" | "empty" | "error"
        - data: list sản phẩm
        - error: chi tiết lỗi kỹ thuật nếu có
        """
        if not self.supabase:
            return {"status": "error", "data": [], "error": "Mất kết nối cơ sở dữ liệu."}

        try:
            query = self.supabase.table("products").select(
                "id, name, description, status, categories(name), materials(name), product_variants(id, sku, color, price, dimensions)"
            ).eq("status", "active")

            response = query.execute()
            raw_products = response.data if response.data else []

            if not raw_products:
                return {"status": "empty", "data": []}

            filtered = []
            
            kw_unaccented = remove_vietnamese_diacritics(keyword).lower() if keyword else ""
            cat_unaccented = remove_vietnamese_diacritics(category).lower() if category else ""
            mat_unaccented = remove_vietnamese_diacritics(material).lower() if material else ""
            color_unaccented = remove_vietnamese_diacritics(color).lower() if color else ""

            # Stop phrases filtering
            stop_phrases = ["toi muon mua", "minh muon mua", "cho minh xem", "cho xem", "co ban", "minh can tim", "toi can tim", "toi tim", "toi can", "chao shop", "shop oi", "xin chao", "cua hang", "cai", "chiec", "bo", "a", "nhi", "khong", "nhe", "co", "tim", "cho"]
            for phrase in stop_phrases:
                kw_unaccented = re.sub(rf'\b{phrase}\b', '', kw_unaccented)
            kw_unaccented = re.sub(r'\s+', ' ', kw_unaccented).strip()

            # Trích xuất các từ quan trọng (bỏ qua từ quá ngắn ngoại trừ các số)
            kw_words = [w for w in kw_unaccented.split() if len(w) > 1 or w.isdigit()]

            for p in raw_products:
                p_name = p.get("name") or ""
                p_name_unaccented = remove_vietnamese_diacritics(p_name).lower()
                
                cat_obj = p.get("categories") or {}
                cat_name = cat_obj.get("name") if isinstance(cat_obj, dict) else ""
                cat_name_unaccented = remove_vietnamese_diacritics(cat_name).lower()

                mat_obj = p.get("materials") or {}
                mat_name = mat_obj.get("name") if isinstance(mat_obj, dict) else ""
                mat_name_unaccented = remove_vietnamese_diacritics(mat_name).lower()

                variants = p.get("product_variants") or []
                variant_prices = [v.get("price") for v in variants if isinstance(v, dict) and v.get("price") is not None]
                variant_colors = [v.get("color") for v in variants if isinstance(v, dict) and v.get("color")]
                variant_colors_unaccented = [remove_vietnamese_diacritics(c).lower() for c in variant_colors]

                # 1. Match Keyword (tên sản phẩm, danh mục, chất liệu, hoặc màu)
                if kw_words:
                    matched_words = [
                        w for w in kw_words 
                        if w in p_name_unaccented 
                        or w in cat_name_unaccented 
                        or w in mat_name_unaccented 
                        or any(w in c for c in variant_colors_unaccented)
                    ]
                    
                    # Nếu người dùng có nhiều từ mô tả cụ thể, tỷ lệ khớp phải cao (ví dụ >= 60%)
                    match_ratio = len(matched_words) / len(kw_words)
                    if len(kw_words) == 1 and match_ratio < 1.0:
                        continue
                    elif len(kw_words) >= 2 and match_ratio < 0.6:
                        continue

                # 2. Match Category
                if cat_unaccented and cat_unaccented not in cat_name_unaccented:
                    continue

                # 3. Match Material
                if mat_unaccented and mat_unaccented not in mat_name_unaccented and mat_unaccented not in p_name_unaccented:
                    continue

                # 4. Match Color
                if color_unaccented and not any(color_unaccented in c for c in variant_colors_unaccented):
                    continue

                # 5. Match Price Filter
                if variant_prices:
                    min_v_price = min(variant_prices)
                    max_v_price = max(variant_prices)
                    if min_price is not None and max_v_price < min_price:
                        continue
                    if max_price is not None and min_v_price > max_price:
                        continue

                p["category_name"] = cat_name
                p["material_name"] = mat_name
                p["price"] = variant_prices[0] if variant_prices else 0
                p["dimensions"] = variants[0].get("dimensions") if variants else None
                p["color"] = variant_colors[0] if variant_colors else None
                p["sku"] = variants[0].get("sku") if variants else None

                filtered.append(p)

            if not filtered:
                return {"status": "empty", "data": []}

            return {"status": "success", "data": filtered[:5]}

        except Exception as e:
            logger.error(f"Lỗi khi truy vấn sản phẩm Supabase: {e}")
            return {"status": "error", "data": [], "error": str(e)}

    def check_attribute_availability(self, product: dict, requested_attribute: str) -> bool:
        if not product or not requested_attribute:
            return True

        req = requested_attribute.lower()
        if "color" in req or "màu" in req:
            variants = product.get("product_variants") or []
            has_color = any(v.get("color") for v in variants if isinstance(v, dict))
            return has_color or bool(product.get("color"))
        
        if "dimension" in req or "kích thước" in req or "size" in req:
            variants = product.get("product_variants") or []
            has_dim = any(v.get("dimensions") for v in variants if isinstance(v, dict))
            return has_dim or bool(product.get("dimensions"))

        if "material" in req or "chất liệu" in req or "loại gỗ" in req:
            mat = product.get("materials") or product.get("material_name")
            return bool(mat)

        if "price" in req or "giá" in req:
            price = product.get("price")
            return price is not None and price > 0

        return True

    def add_to_cart(self, session_id: str, sku: str, quantity: int = 1) -> dict:
        if quantity <= 0: return {"status": "error", "message": "Số lượng phải lớn hơn 0."}
        if not self.supabase: return {"status": "error", "message": "Hệ thống mất kết nối cơ sở dữ liệu."}
        try:
            var_res = self.supabase.table("product_variants").select("id, price, product_id").eq("sku", sku).execute()
            if not var_res.data: return {"status": "error", "message": "Mã sản phẩm không tồn tại."}
            variant_data = var_res.data[0]
            prod_res = self.supabase.table("products").select("name").eq("id", variant_data["product_id"]).execute()
            product_name = prod_res.data[0]["name"] if prod_res.data else "Sản phẩm"
            existing = self.supabase.table("cart_items").select("id, quantity").eq("session_id", session_id).eq("product_variant_sku", sku).execute()
            if existing.data:
                new_qty = existing.data[0]["quantity"] + quantity
                self.supabase.table("cart_items").update({"quantity": new_qty}).eq("id", existing.data[0]["id"]).execute()
            else:
                self.supabase.table("cart_items").insert({"session_id": session_id, "product_variant_sku": sku, "product_name": product_name, "price_at_addition": variant_data["price"], "quantity": quantity}).execute()
            return {"status": "success", "message": f"Đã thêm {quantity} {product_name} vào giỏ hàng."}
        except Exception:
            return {"status": "error", "message": "Không thể thêm vào giỏ hàng lúc này, vui lòng thử lại."}

    def view_cart(self, session_id: str) -> dict:
        if not self.supabase: return {"status": "error", "message": "Hệ thống mất kết nối cơ sở dữ liệu."}
        try:
            res = self.supabase.table("cart_items").select("product_name, product_variant_sku, quantity, price_at_addition").eq("session_id", session_id).execute()
            if not res.data: return {"status": "empty", "message": "Giỏ hàng của bạn đang trống."}
            total = sum(float(item["price_at_addition"]) * item["quantity"] for item in res.data)
            return {"status": "success", "items": res.data, "total": total}
        except Exception:
            return {"status": "error", "message": "Không thể tải dữ liệu giỏ hàng lúc này."}

    def find_stores(self, keyword: str = None, lat: float = None, lng: float = None, limit: int = 5) -> list:
        if not self.supabase: return []
        try:
            query = self.supabase.table("stores").select("id, address, ward, district, city, latitude, longitude, phone, supplier_id, suppliers(business_name)")
            if lat and lng:
                response = query.execute()
                data = response.data if response.data else []
                for item in data:
                    item["distance"] = self._haversine_distance(lat, lng, item.get("latitude", 0), item.get("longitude", 0))
                data.sort(key=lambda x: x["distance"])
            elif keyword:
                search_term = f"%{keyword}%"
                response = query.or_(f"address.ilike.{search_term},ward.ilike.{search_term},district.ilike.{search_term},city.ilike.{search_term}").execute()
                data = response.data if response.data else []
            else:
                return []
            results = []
            for item in data[:limit]:
                supplier_obj = item.get("suppliers")
                biz_name = supplier_obj[0].get("business_name") if isinstance(supplier_obj, list) and supplier_obj else "Xưởng WoodHub"
                results.append({"id": item.get("id"), "supplier_name": biz_name, "address": f"{item.get('address')}, {item.get('ward')}, {item.get('district')}, {item.get('city')}", "distance": item.get("distance", "N/A"), "type": "store"})
            return results
        except Exception as e:
            return []

    def estimate_custom_3d(self, wood_type: str, w: float, h: float, d: float) -> dict:
        if w <= 0 or h <= 0 or d <= 0: return {"status": "error", "message": "Kích thước (dài, rộng, cao) phải lớn hơn 0."}
        base_price_per_m3 = 10_000_000
        volume_m3 = (w * h * d) / 1_000_000
        coeff = self.WOOD_COEFFICIENTS.get(wood_type.lower(), 1.0)
        estimated_price = volume_m3 * base_price_per_m3 * coeff
        formatted_price = f"{int(estimated_price):,.0f} VND".replace(",", ".")
        return {"status": "success", "estimated_price": round(estimated_price, 0), "price_display": formatted_price, "message": f"Với kích thước {w}x{h}x{d}cm và gỗ {wood_type}, giá ước tính là {formatted_price}."}

    def redirect_to_payment(self) -> dict:
        return {"status": "success", "link": "https://woodhub.id.vn/payment"}

business_engine = BusinessEngine()