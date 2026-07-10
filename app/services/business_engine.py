def search_product(self, keyword: str) -> list:
        """
        Tìm kiếm sản phẩm tương đối theo từ khóa (Fuzzy Search)
        Tuân thủ nghiêm ngặt giới hạn tối đa 5 sản phẩm khớp nhất, không tự bịa dữ liệu.
        """
        if not self.supabase:
            logger.error("Supabase Client chưa được khởi tạo.")
            return []
            
        if not keyword or len(keyword.strip()) < 2:
            return []

        try:
            # Thực hiện truy vấn tương đối (ILike) không phân biệt hoa thường
            # Đồng bộ toàn bộ cấu trúc cột mới bao gồm category_id và trường price để render Card
            response = self.supabase.table("products") \
                .select("id, name, product_id, category_id, supplier_id, material_id, description, status, price") \
                .ilike("name", f"%{keyword.strip()}%") \
                .limit(5) \
                .execute()
                
            # Đảm bảo trả về mảng dữ liệu sạch từ Database thật
            return response.data if response.data else []

        except Exception as e:
            logger.error(f"Lỗi khi thực hiện Fuzzy Search trên bảng products: {str(e)}")
            return []