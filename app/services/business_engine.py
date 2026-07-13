import math
import logging
import re
from app.core.database import supabase

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

    def search_product(self, keyword: str, price_min: float = None, price_max: float = None) -> dict:
        """
        Tìm kiếm lai: Kết hợp NLP Stop-words thủ công + Inner Join lọc giá.
        Trả về dict: {"is_fallback": bool, "data": list}
        """
        if not self.supabase or not keyword:
            return {"is_fallback": False, "data": []}

        # 1. TIỀN XỬ LÝ TỪ KHÓA (Lọc từ rác nếu AI trả về chuỗi còn nhiễu)
        clean_text = re.sub(r'[.,!?]', ' ', keyword.lower())
        stop_phrases = ["tôi muốn mua", "mình muốn mua", "cho mình xem", "cho xem", "có bán", "mình cần tìm", "tôi cần tìm", "tôi tìm", "tôi cần", "chào shop", "shop ơi", "xin chào", "cửa hàng", "cái", "chiếc", "bộ", "ạ", "nhỉ", "không", "nhé", "có"]
        padded_text = f" {clean_text} "
        for phrase in stop_phrases:
            padded_text = padded_text.replace(f" {phrase} ", " ")
        final_keyword = re.sub(r'\s+', ' ', padded_text).strip()

        if len(final_keyword) < 2:
            return {"is_fallback": False, "data": []}

        # 2. TRUY VẤN LỌC GIÁ TIÊU CHUẨN
        try:
            # Lấy data và map variants lên cấp 1 để logic client không đổi
            query = self.supabase.table("products").select("id, name, description, status, product_variants!inner(price)")
            query = query.ilike("name", f"%{final_keyword}%")
            
            if price_min is not None:
                query = query.gte("product_variants.price", price_min)
            if price_max is not None:
                query = query.lte("product_variants.price", price_max)
                
            response = query.limit(5).execute()
            raw_data = response.data if response.data else []

            def format_data(data_list):
                processed = []
                for p in data_list:
                    variants = p.get("product_variants")
                    p["price"] = variants[0].get("price", 0) if variants and isinstance(variants, list) else 0
                    if "product_variants" in p:
                        del p["product_variants"]
                    processed.append(p)
                return processed

            if raw_data:
                return {"is_fallback": False, "data": format_data(raw_data)}

            # 3. CƠ CHẾ FALLBACK (Chỉ kích hoạt nếu có điều kiện giá mà tìm không thấy)
            if price_min is not None or price_max is not None:
                fb_query = self.supabase.table("products").select("id, name, description, status, product_variants!inner(price)").ilike("name", f"%{final_keyword}%").limit(20).execute()
                fb_data = fb_query.data if fb_query.data else []
                
                if not fb_data:
                    return {"is_fallback": False, "data": []}
                
                target_price = ((price_min + price_max) / 2) if (price_min and price_max) else (price_min or price_max)
                
                def price_diff(item):
                    v = item.get("product_variants", [])
                    return abs(v[0].get("price", 0) - target_price) if v else float('inf')

                fb_data.sort(key=price_diff)
                return {"is_fallback": True, "data": format_data(fb_data[:3])}

            return {"is_fallback": False, "data": []}

        except Exception as e:
            logger.error(f"Lỗi khi tìm sản phẩm & lọc giá: {str(e)}")
            return {"is_fallback": False, "data": []}

    def add_to_cart(self, session_id: str, sku: str, quantity: int = 1) -> dict:
        # [GIỮ NGUYÊN CODE CỦA BẠN]
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
        # [GIỮ NGUYÊN CODE CỦA BẠN]
        if not self.supabase: return {"status": "error", "message": "Hệ thống mất kết nối cơ sở dữ liệu."}
        try:
            res = self.supabase.table("cart_items").select("product_name, product_variant_sku, quantity, price_at_addition").eq("session_id", session_id).execute()
            if not res.data: return {"status": "empty", "message": "Giỏ hàng của bạn đang trống."}
            total = sum(float(item["price_at_addition"]) * item["quantity"] for item in res.data)
            return {"status": "success", "items": res.data, "total": total}
        except Exception:
            return {"status": "error", "message": "Không thể tải dữ liệu giỏ hàng lúc này."}

    def find_stores(self, keyword: str = None, lat: float = None, lng: float = None, limit: int = 5) -> list:
        # [GIỮ NGUYÊN CODE CỦA BẠN ĐÃ TỐI ƯU Ở PHẦN TRƯỚC]
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
        # [GIỮ NGUYÊN CODE CỦA BẠN]
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