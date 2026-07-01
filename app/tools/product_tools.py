# app/tools/product_tools.py

from app.core.database import supabase

def get_product_details_by_name(product_name: str) -> str:
    """
    Tra cứu thông tin sản phẩm bằng cách tìm kiếm linh hoạt trên SKU và Tên sản phẩm.
    """
    try:
        clean_name = product_name.strip().lower()
        if not clean_name:
            return "Vui lòng cung cấp từ khóa sản phẩm để tìm kiếm."

        # 1. TÌM KIẾM THEO SKU (Ưu tiên cao nhất)
        # Tìm các sản phẩm có SKU khớp với từ khóa (dùng ilike để tìm tương đối)
        variant_res = supabase.table("product_variants") \
            .select("product_id, sku, color, dimensions, price, stock_quantity") \
            .ilike("sku", f"%{clean_name}%") \
            .execute()

        product_id = None
        if variant_res.data:
            product_id = variant_res.data[0]["product_id"]

        # 2. NẾU KHÔNG THẤY SKU, THỬ TÌM THEO TÊN SẢN PHẨM
        if not product_id:
            prod_res = supabase.table("products") \
                .select("id, name, description") \
                .ilike("name", f"%{clean_name}%") \
                .execute()
            if prod_res.data:
                product_id = prod_res.data[0]["id"]
                target_product = prod_res.data[0]
            else:
                return f"Xin lỗi, mình không tìm thấy sản phẩm nào khớp với từ khóa '{product_name}'."
        else:
            # Nếu tìm thấy theo SKU, lấy thông tin product cha
            prod_res = supabase.table("products").select("name, description").eq("id", product_id).execute()
            target_product = prod_res.data[0] if prod_res.data else {"name": "Sản phẩm tìm thấy theo SKU", "description": ""}

        # 3. LẤY CHI TIẾT TẤT CẢ CÁC BIẾN THỂ CỦA SẢN PHẨM ĐÓ
        var_res = supabase.table("product_variants").select("sku, color, dimensions, price, stock_quantity").eq("product_id", product_id).execute()
        img_res = supabase.table("product_images").select("url").eq("product_id", product_id).eq("is_primary", True).execute()

        # 4. XÂY DỰNG NỘI DUNG PHẢN HỒI
        output = f"Thông tin sản phẩm: {target_product.get('name')}\n"
        output += f"Mô tả: {target_product.get('description') or 'Đang cập nhật'}\n"

        if var_res.data:
            output += "Các phiên bản sẵn có tại kho:\n"
            for v in var_res.data:
                price_val = float(v.get('price') or 0)
                status = "Còn hàng" if (v.get('stock_quantity') or 0) > 0 else "Tạm hết hàng"
                output += (
                    f"- SKU: {v.get('sku')} | Màu: {v.get('color') or 'Tiêu chuẩn'} | "
                    f"Kích thước: {v.get('dimensions') or 'N/A'} | "
                    f"Giá: {price_val:,.0f} VND | {status}\n"
                )

        if img_res.data:
            output += f"Ảnh sản phẩm: {img_res.data[0].get('url')}\n"

        return output

    except Exception as e:
        return f"Lỗi truy vấn dữ liệu: {str(e)}"


def redirect_to_payment_page() -> str:
    return "Hệ thống WoodHub đã tạo hóa đơn. Bạn truy cập link này để thanh toán: https://woodhub.id.vn/payment"

def add_to_cart(session_id: str, sku: str, product_name: str, price: float, quantity: int = 1) -> str:
    try:
        existing = supabase.table("cart_items").select("id, quantity").eq("session_id", session_id).eq("product_variant_sku", sku).execute()
        if existing.data:
            new_qty = existing.data[0]["quantity"] + quantity
            supabase.table("cart_items").update({"quantity": new_qty}).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("cart_items").insert({
                "session_id": session_id, "product_variant_sku": sku, "product_name": product_name,
                "price_at_addition": price, "quantity": quantity
            }).execute()
        return f"Đã thêm {product_name} (SKU: {sku}) vào giỏ hàng."
    except Exception as e:
        return f"Lỗi giỏ hàng: {str(e)}"

def view_cart(session_id: str) -> str:
    try:
        res = supabase.table("cart_items").select("product_name, product_variant_sku, quantity, price_at_addition").eq("session_id", session_id).execute()
        if not res.data: return "Giỏ hàng trống."
        output = "🛒 GIỎ HÀNG:\n"
        total = 0
        for item in res.data:
            subtotal = float(item['price_at_addition']) * item['quantity']
            total += subtotal
            output += f"- {item['product_name']} (SKU: {item['product_variant_sku']}) | SL: {item['quantity']} | Giá: {float(item['price_at_addition']):,.0f}đ\n"
        output += f"💰 Tổng: {total:,.0f} VND"
        return output
    except Exception as e:
        return f"Lỗi xem giỏ hàng: {str(e)}"

def estimate_custom_3d_product(wood_type: str, length: float, width: float, height: float) -> dict:
    try:
        volume = (length * width * height) / 1000000
        estimated_price = round(volume * 15000000 * 1.25, -3) # Đơn giá 15tr/m3 + 25% công
        return {
            "status": "success",
            "estimated_price": max(estimated_price, 500000),
            "message": f"Giá ước tính cho thiết kế {length}x{width}x{height}cm gỗ {wood_type} là {max(estimated_price, 500000):,.0f} VNĐ."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}