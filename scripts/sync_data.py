import os
import json
import requests  # Thay thế supabase bằng requests để vượt tường lửa
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

# 1. Cấu hình
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

# Khởi tạo mô hình Embedding
model = SentenceTransformer('keepitreal/vietnamese-sbert')

def sync_products():
    print("🚀 Bắt đầu quá trình đồng bộ dữ liệu qua API trực tiếp...")
    
    # 2. Fetch dữ liệu từ Supabase qua REST API
    # Dùng requests.get thay vì supabase.table().select()
    url = f"{SUPABASE_URL}/rest/v1/product_variants"
    params = {
        "select": "id, sku, color, dimensions, price, stock_quantity, products(name,description,categories(name),materials(name)),product_images(url)"
    }
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        products_data = response.json()
        
        if not products_data:
            print("⚠️ Không có dữ liệu để đồng bộ.")
            return

        documents = []
        
        for item in products_data:
            product = item.get("products", {}) or {}
            category = product.get("categories", {}).get("name", "Chưa phân loại") if product.get("categories") else "Chưa phân loại"
            material = product.get("materials", {}).get("name", "Chưa rõ") if product.get("materials") else "Chưa rõ"
            images = [img["url"] for img in (item.get("product_images") or [])]
            img_url = images[0] if images else ""

            content = (f"Sản phẩm: {product.get('name', 'Không tên')}. Danh mục: {category}. Chất liệu: {material}. "
                       f"Mô tả: {product.get('description', '')}. Màu sắc: {item.get('color', 'N/A')}. "
                       f"Kích thước: {item.get('dimensions', 'N/A')}. Giá: {item.get('price', 0)} VND. "
                       f"SKU: {item.get('sku', 'N/A')}.")
            
            embedding_vector = model.encode(content).tolist()

            # A. Chuẩn bị cho FAISS
            documents.append({
                "id": item["id"],
                "sku": item["sku"],
                "content": content,
                "metadata": {
                    "name": product.get('name'),
                    "price": item.get('price'),
                    "stock": item.get('stock_quantity'),
                    "image_url": img_url
                }
            })

            # B. Cập nhật Vector lên cloud dùng requests.patch
            try:
                update_url = f"{SUPABASE_URL}/rest/v1/product_variants?id=eq.{item['id']}"
                requests.patch(update_url, headers=headers, json={'embedding': embedding_vector})
            except Exception as e:
                print(f"❌ Lỗi update vector ID {item['id']}: {e}")

        # C. Lưu file local
        output_path = "data/documents.json"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Đã hoàn tất! {len(documents)} sản phẩm đã được đồng bộ.")

    except Exception as e:
        print(f"❌ Lỗi nghiêm trọng: {e}")
        print("💡 Mẹo: Hãy kiểm tra lại URL trong file .env, đảm bảo không có dấu / ở cuối.")

if __name__ == "__main__":
    sync_products()