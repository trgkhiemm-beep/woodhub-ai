import math
import logging
import re
from app.core.database import supabase

logger = logging.getLogger("woodhub.business_engine")

class BusinessEngine:
    # Hệ số giá vật liệu gỗ tiêu chuẩn của hệ thống
    WOOD_COEFFICIENTS = {
        "sồi": 1.0,      
        "óc chó": 2.5,   
        "tần bì": 1.1,   
        "thông": 0.7,    
    }

    def __init__(self):
        """
        Khởi tạo và đóng gói Supabase Client từ core database module.
        Chuyển đổi từ mô hình Static sang Singleton Instance để quản lý state an toàn.
        """
        self.supabase = supabase
        if not self.supabase:
            logger.error("Hệ thống nghiêm trọng: Supabase Client chưa được nạp từ app.core.database!")

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Tính toán khoảng cách địa lý giữa 2 tọa độ theo công thức Haversine"""
        R = 6371  
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)

    def search_product(self, keyword: str) -> list:
        """
        Tìm kiếm sản phẩm theo từ khóa (Tích hợp Bộ lọc NLP Stop-words thủ công).
        Xử lý cắt gọt câu dài để lấy chính xác danh từ tìm kiếm.
        """
        import re 

        if not self.supabase:
            logger.error("Hủy truy vấn: Supabase Client chưa được khởi tạo.")
            return []
            
        if not keyword:
            return []

        # ==========================================
        # LỚP 2: TIỀN XỬ LÝ & BÓC TÁCH TỪ KHÓA (NLP)
        # ==========================================
        clean_text = keyword.lower()
        
        # 1. Xóa các dấu câu đặc biệt (thay bằng khoảng trắng)
        clean_text = re.sub(r'[.,!?]', ' ', clean_text)
        
        # 2. Danh sách cụm từ rác (Stop-phrases) xếp từ dài đến ngắn để tránh cắt nhầm
        stop_phrases = [
            "tôi muốn mua", "mình muốn mua", "cho mình xem", "cho xem",
            "có bán", "mình cần tìm", "tôi cần tìm", "tôi tìm", "tôi cần",
            "chào shop", "shop ơi", "xin chào", "cửa hàng",
            "cái", "chiếc", "bộ", "ạ", "nhỉ", "không", "nhé", "có"
        ]
        
        # 3. Kỹ thuật đệm khoảng trắng (Padding Space): 
        # Đệm 2 đầu để tìm chính xác từ độc lập, không cắt nhầm chữ bên trong từ khác.
        padded_text = f" {clean_text} "
        for phrase in stop_phrases:
            padded_text = padded_text.replace(f" {phrase} ", " ")
        
        # 4. Gom các khoảng trắng thừa lại thành 1 và cắt sạch 2 đầu
        final_keyword = re.sub(r'\s+', ' ', padded_text).strip()

        # 5. Rào chắn (Guard Clause): Nếu sau khi cắt gọt chuỗi bị rỗng -> Bỏ qua an toàn
        if len(final_keyword) < 2:
            return []

        # ==========================================
        # TRUY VẤN DATABASE SUPABASE VỚI TỪ KHÓA ĐÃ LỌC
        # ==========================================
        try:
            response = self.supabase.table("products") \
                .select("id, name, description, status, product_variants(price)") \
                .ilike("name", f"%{final_keyword}%") \
                .limit(5) \
                .execute()
                
            raw_data = response.data if response.data else []
            processed_data = []

            for product in raw_data:
                variants = product.get("product_variants")
                
                if variants and isinstance(variants, list) and len(variants) > 0:
                    product["price"] = variants[0].get("price", 0)
                else:
                    product["price"] = 0 

                if "product_variants" in product:
                    del product["product_variants"]

                processed_data.append(product)

            return processed_data

        except Exception as e:
            logger.error(f"Lỗi khi thực hiện Fuzzy Search: {str(e)}")
            return []

    def add_to_cart(self, session_id: str, sku: str, quantity: int = 1) -> dict:
        """Thêm sản phẩm vào giỏ hàng của người dùng"""
        if quantity <= 0:
            return {"status": "error", "message": "Số lượng phải lớn hơn 0."}

        if not self.supabase:
            return {"status": "error", "message": "Hệ thống mất kết nối cơ sở dữ liệu."}

        try:
            var_res = (
                self.supabase.table("product_variants")
                .select("id, price, product_id")
                .eq("sku", sku)
                .execute()
            )
            if not var_res.data:
                return {"status": "error", "message": "Mã sản phẩm không tồn tại."}

            variant_data = var_res.data[0]
            prod_res = (
                self.supabase.table("products")
                .select("name")
                .eq("id", variant_data["product_id"])
                .execute()
            )
            product_name = prod_res.data[0]["name"] if prod_res.data else "Sản phẩm"

            existing = (
                self.supabase.table("cart_items")
                .select("id, quantity")
                .eq("session_id", session_id)
                .eq("product_variant_sku", sku)
                .execute()
            )

            if existing.data:
                new_qty = existing.data[0]["quantity"] + quantity
                self.supabase.table("cart_items").update({"quantity": new_qty}).eq(
                    "id", existing.data[0]["id"]
                ).execute()
            else:
                self.supabase.table("cart_items").insert(
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
            logger.exception("Lỗi hệ thống tại add_to_cart(session_id=%s, sku=%s)", session_id, sku)
            return {"status": "error", "message": "Không thể thêm vào giỏ hàng lúc này, vui lòng thử lại."}

    def view_cart(self, session_id: str) -> dict:
        """Xem danh sách và tổng tiền giỏ hàng hiện tại"""
        if not self.supabase:
            return {"status": "error", "message": "Hệ thống mất kết nối cơ sở dữ liệu."}

        try:
            res = (
                self.supabase.table("cart_items")
                .select("product_name, product_variant_sku, quantity, price_at_addition")
                .eq("session_id", session_id)
                .execute()
            )
        except Exception:
            logger.exception("Lỗi hệ thống tại view_cart(session_id=%s)", session_id)
            return {"status": "error", "message": "Không thể tải dữ liệu giỏ hàng lúc này."}

        if not res.data:
            return {"status": "empty", "message": "Giỏ hàng của bạn đang trống."}

        total = sum(float(item["price_at_addition"]) * item["quantity"] for item in res.data)
        return {"status": "success", "items": res.data, "total": total}

    def find_stores(self, keyword: str = None, lat: float = None, lng: float = None, limit: int = 5) -> list:
        """
        Phương thức tìm kiếm lai (Hybrid Search):
        - Nếu có lat/lng: Tìm theo tọa độ (Gần nhất).
        - Nếu có keyword: Tìm theo Tên xưởng (business_name) hoặc Địa chỉ (ward, district, city).
        """
        if not self.supabase:
            return []

        try:
            # 1. Khởi tạo query base
            query = self.supabase.table("stores").select(
                "id, address, ward, district, city, latitude, longitude, phone, supplier_id, suppliers(business_name)"
            )

            # 2. Xử lý Logic Tìm kiếm
            if lat and lng:
                # Nếu có tọa độ -> Lấy toàn bộ (hoặc một subset lớn) để client tính khoảng cách hoặc filter sơ bộ
                # Lưu ý: Supabase không hỗ trợ PostGIS trực tiếp qua SDK nên ta lấy tất cả và lọc bằng Python (như cũ)
                response = query.execute()
                data = response.data if response.data else []
                
                # Tính khoảng cách và gắn vào object
                for item in data:
                    item["distance"] = self._haversine_distance(lat, lng, item.get("latitude", 0), item.get("longitude", 0))
                
                # Sắp xếp theo khoảng cách
                data.sort(key=lambda x: x["distance"])
            
            elif keyword:
                # Tìm theo từ khóa (Tên xưởng hoặc Địa chỉ)
                # Dùng .or_ để tìm trên nhiều cột cùng lúc
                search_term = f"%{keyword}%"
                response = query.or_(f"address.ilike.{search_term},ward.ilike.{search_term},district.ilike.{search_term},city.ilike.{search_term}") \
                                .execute()
                data = response.data if response.data else []
            else:
                return []

            # 3. Chuẩn hóa format trả về (Gộp tên Supplier vào)
            results = []
            for item in data[:limit]:
                supplier_obj = item.get("suppliers")
                biz_name = supplier_obj[0].get("business_name") if isinstance(supplier_obj, list) and supplier_obj else "Xưởng WoodHub"
                
                results.append({
                    "id": item.get("id"),
                    "supplier_name": biz_name,
                    "address": f"{item.get('address')}, {item.get('ward')}, {item.get('district')}, {item.get('city')}",
                    "distance": item.get("distance", "N/A"),
                    "type": "store"
                })
            return results

        except Exception as e:
            logger.error(f"Lỗi tìm kiếm cửa hàng: {str(e)}")
            return []

    def estimate_custom_3d(self, wood_type: str, w: float, h: float, d: float) -> dict:
        """Tính toán giá tiền ước tính dựa trên thể tích cấu tạo và hệ số chất liệu gỗ"""
        if w <= 0 or h <= 0 or d <= 0:
            return {"status": "error", "message": "Kích thước (dài, rộng, cao) phải lớn hơn 0."}

        base_price_per_m3 = 10_000_000
        volume_m3 = (w * h * d) / 1_000_000

        coeff = self.WOOD_COEFFICIENTS.get(wood_type.lower(), 1.0)
        estimated_price = volume_m3 * base_price_per_m3 * coeff

        formatted_price = f"{int(estimated_price):,.0f} VND".replace(",", ".")

        return {
            "status": "success",
            "estimated_price": round(estimated_price, 0),
            "price_display": formatted_price,
            "message": f"Với kích thước {w}x{h}x{d}cm và gỗ {wood_type}, giá ước tính là {formatted_price}.",
        }

    def redirect_to_payment(self) -> dict:
        """Trả về đường dẫn cổng thanh toán của hệ thống WoodHub"""
        return {"status": "success", "link": "https://woodhub.id.vn/payment"}


# Khởi tạo đối tượng mẫu duy nhất (Singleton Instance) để phục vụ import từ app/api/chat.py
business_engine = BusinessEngine()