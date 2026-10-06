"""Câu trả lời cố định (đã thống nhất với sản phẩm) — dùng chung cho tools, planner và composer."""

OUT_OF_SCOPE = "Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ trên WoodHub."
NO_INFO = "Hiện hệ thống chưa cập nhật thông tin phù hợp."
PRODUCT_API_ERROR = "Hệ thống chưa thể kiểm tra dữ liệu sản phẩm lúc này."
SYSTEM_ERROR = "Hệ thống tạm thời không phản hồi, vui lòng thử lại sau."
READ_ONLY = ("Trợ lý AI chỉ hỗ trợ tra cứu và tư vấn, không thay đổi dữ liệu. "
             "Vui lòng cập nhật qua trang quản trị của WoodHub.")
BACKEND_DENIED = ("Hệ thống WoodHub chưa cho phép xem thông tin này. Nếu đây là thông tin tài khoản của bạn, "
                  "vui lòng đăng nhập trên WoodHub rồi thử lại.")
ACTIONS_DISABLED = "Trợ lý AI không còn tạo hay thực hiện thay đổi dữ liệu nên không có yêu cầu nào để xác nhận/hủy."

# Tool đọc dữ liệu sản phẩm/nhà cung cấp: lỗi hạ tầng → PRODUCT_API_ERROR, không có dữ liệu → NO_INFO.
PRODUCT_TOOLS = frozenset({"get_product", "recommend_products", "compare_products", "get_inventory", "get_supplier_info"})
