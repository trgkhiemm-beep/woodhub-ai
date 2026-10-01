"""Câu trả lời cố định (đã thống nhất với sản phẩm) — dùng chung cho tools, planner và composer."""

OUT_OF_SCOPE = "Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ của cửa hàng."
NO_PRODUCT = "Hiện hệ thống chưa cập nhật sản phẩm phù hợp."
PRODUCT_API_ERROR = "Hệ thống chưa thể kiểm tra dữ liệu sản phẩm lúc này."
SYSTEM_ERROR = "Hệ thống tạm thời không phản hồi, vui lòng thử lại sau."

# Tool đọc dữ liệu sản phẩm: lỗi hạ tầng → PRODUCT_API_ERROR, không có dữ liệu → NO_PRODUCT.
PRODUCT_TOOLS = frozenset({"get_product", "recommend_products", "compare_products", "get_inventory"})
