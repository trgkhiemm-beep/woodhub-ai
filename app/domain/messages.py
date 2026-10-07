"""Câu trả lời cố định (đã thống nhất với sản phẩm) — dùng chung cho tools, planner và composer."""

OUT_OF_SCOPE = "Xin lỗi, tôi chỉ hỗ trợ thông tin và dịch vụ trên WoodHub."
NO_INFO = "Hiện hệ thống chưa cập nhật thông tin phù hợp."
API_ERROR = "Hiện hệ thống chưa thể kiểm tra thông tin này."          # Backend lỗi/timeout/dữ liệu hỏng
PRODUCT_API_ERROR = API_ERROR
SYSTEM_ERROR = API_ERROR
UPSTREAM_BUSY = "Hệ thống WoodHub đang bận, vui lòng thử lại sau ít phút."  # Backend trả 429 cho Agent
AGENT_BUSY = "Trợ lý AI đang nhận quá nhiều yêu cầu, vui lòng thử lại sau ít phút."  # rate limit của chính Agent
READ_ONLY = ("Trợ lý AI chỉ hỗ trợ tra cứu và tư vấn, không thay đổi dữ liệu. "
             "Vui lòng cập nhật qua trang quản trị của WoodHub.")
BACKEND_DENIED = ("Hệ thống WoodHub chưa cho phép xem thông tin này. Nếu đây là thông tin tài khoản của bạn, "
                  "vui lòng đăng nhập trên WoodHub rồi thử lại.")
ACTIONS_DISABLED = "Trợ lý AI không còn tạo hay thực hiện thay đổi dữ liệu nên không có yêu cầu nào để xác nhận/hủy."

# NO DATA → NO_INFO · API ERROR → API_ERROR · Backend 429 → UPSTREAM_BUSY (không đổi mọi lỗi thành 'không có dữ liệu').
PRODUCT_TOOLS = frozenset({"get_product", "recommend_products", "search_products", "compare_products", "get_inventory", "get_supplier_info"})
