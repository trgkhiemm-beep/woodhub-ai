import math
import logging
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
        Tìm kiếm sản phẩm theo từ khóa.
        Sử dụng Resource Embedding để lấy price từ product_variants.
        Đã loại bỏ các cột không tồn tại và không cần thiết để tránh lỗi 42703.
        """
        if not self.supabase:
            logger.error("Hủy truy vấn: Supabase Client chưa được khởi tạo.")
            return []
            
        if not keyword or len(keyword.strip()) < 2:
            return []

        try:
            # Truy vấn tinh giản: Chỉ lấy đúng những gì cần thiết cho UI
            response = self.supabase.table("products") \
                .select("id, name, description, status, product_variants(price)") \
                .ilike("name", f"%{keyword.strip()}%") \
                .limit(5) \
                .execute()
                
            raw_data = response.data if response.data else []
            processed_data = []

            for product in raw_data:
                # Trích xuất price từ mảng lồng product_variants
                variants = product.get("product_variants")
                
                # Logic lấy giá an toàn: ưu tiên biến thể đầu tiên, fallback về 0
                if variants and isinstance(variants, list) and len(variants) > 0:
                    product["price"] = variants[0].get("price", 0)
                else:
                    product["price"] = 0 

                # Xóa key thừa sau khi đã map xong dữ liệu
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

    def find_suppliers_nearby(self, lat: float, lng: float, max_dist_km: float = 50.0) -> list:
        """
        Tìm kiếm tối đa 5 xưởng/showroom gần vị trí người dùng nhất trong bán kính cho phép.
        Sử dụng kỹ thuật Supabase Resource Embedding để kết hợp lấy tên nhà cung cấp từ bảng 'suppliers'.
        Mã hóa an toàn chống crash dữ liệu khi tọa độ trống (Null).
        """
        import math

        if not self.supabase:
            logger.error("Supabase Client chưa được khởi tạo.")
            return []

        try:
            # 1. Thực hiện một truy vấn duy nhất quét bảng stores và nhúng dữ liệu từ bảng suppliers
            response = self.supabase.table("stores") \
                .select(
                    "id, address, ward, district, city, latitude, longitude, phone, supplier_type, supplier_id, "
                    "suppliers(business_name)"
                ) \
                .execute()
            
            stores_data = response.data if response.data else []
            nearby_suppliers = []

            # 2. Quét qua danh sách các cửa hàng để tính toán khoảng cách hình học
            for store in stores_data:
                store_lat = store.get("latitude")
                store_lng = store.get("longitude")
                
                # EDGE CASE: Bỏ qua an toàn nếu bản ghi store bị Null tọa độ địa lý trên DB
                if store_lat is None or store_lng is None:
                    continue

                # 3. Thuật toán hình học Haversine tính khoảng cách bề mặt cầu (Đơn vị: km)
                # Bán kính Trái Đất trung bình = 6371.0 km
                R = 6371.0
                
                phi1 = math.radians(lat)
                phi2 = math.radians(store_lat)
                delta_phi = math.radians(store_lat - lat)
                delta_lambda = math.radians(store_lng - lng)
                
                a = math.sin(delta_phi / 2.0) ** 2 + \
                    math.cos(phi1) * math.cos(phi2) * \
                    math.sin(delta_lambda / 2.0) ** 2
                
                c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
                distance = R * c

                # Lọc các cửa hàng nằm trong bán kính cấu hình (mặc định 50km)
                if distance <= max_dist_km:
                    # 4. Trích xuất thông tin tên xưởng một cách an toàn từ object lồng nhau (Embedding)
                    supplier_obj = store.get("suppliers")
                    business_name = "Xưởng WoodHub"  # Giá trị dự phòng (Fallback) nếu quan hệ bị mồ côi
                    
                    if isinstance(supplier_obj, dict):
                        business_name = supplier_obj.get("business_name") or "Xưởng WoodHub"
                    elif isinstance(supplier_obj, list) and len(supplier_obj) > 0:
                        # Trường hợp cấu hình DB trả ra dạng mảng
                        business_name = supplier_obj[0].get("business_name") or "Xưởng WoodHub"

                    # 5. Chuẩn hóa chuỗi địa chỉ hành chính hiển thị trực quan cho người dùng/AI
                    addr_parts = [
                        store.get("address"),
                        store.get("ward"),
                        store.get("district"),
                        store.get("city")
                    ]
                    full_address = ", ".join([p.strip() for p in addr_parts if p and p.strip()])

                    # 6. Map dữ liệu đầu ra chuẩn format context cũ để an toàn cho AI và Frontend
                    nearby_suppliers.append({
                        "id": store.get("id"),
                        "supplier_name": business_name,
                        "address": full_address if full_address else "Địa chỉ đang cập nhật",
                        "phone": store.get("phone") or "Chưa cập nhật",
                        "lat": float(store_lat),
                        "lng": float(store_lng),
                        "distance": round(distance, 2)  # Làm tròn 2 chữ số thập phân
                    })

            # 7. Sắp xếp danh sách theo khoảng cách tăng dần (gần nhất xếp lên đầu)
            nearby_suppliers.sort(key=lambda x: x["distance"])
            
            # Giới hạn nghiêm ngặt tối đa 5 phần tử để tiết kiệm Token Context
            return nearby_suppliers[:5]

        except Exception as e:
            logger.error(f"Lỗi hệ thống khi tìm kiếm xưởng gần nhất: {str(e)}")
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