# app/orchestrator/analyzer.py
import re

def analyze_intent_and_entities(user_msg: str) -> dict:
    """
    Phân tích ý định và trích xuất thực thể bằng Rule-based siêu tốc
    nhằm tiết kiệm chi phí Token cho doanh nghiệp.
    """
    msg_lower = user_msg.lower()
    
    # Mặc định
    result = {
        "intent": "GENERAL_KNOWLEDGE",
        "entities": {}
    }
    
    # 1. Intent Detection
    if any(kw in msg_lower for kw in ["thiết kế", "3d", "dựng hình", "bản vẽ", "meshy"]):
        result["intent"] = "DESIGN_3D"
    elif any(kw in msg_lower for kw in ["tìm", "mua", "giá", "sản phẩm", "bàn", "ghế", "tủ", "sofa"]):
        result["intent"] = "SEARCH_PRODUCT"
    elif any(kw in msg_lower for kw in ["giỏ hàng", "thêm vào", "thanh toán", "mua cái này"]):
        result["intent"] = "CART_ACTION"
        
    # 2. Entity Extraction (Kích thước, Chất liệu...)
    # Trích xuất chất liệu
    materials = ["gỗ sồi", "gỗ công nghiệp", "mdf", "gỗ cao su", "gỗ óc chó"]
    for mat in materials:
        if mat in msg_lower:
            result["entities"]["material"] = mat
            break
            
    # Trích xuất kích thước bằng Regex (Ví dụ: 1m2, 2m, 120cm)
    size_match = re.search(r'(\d+[\.,]?\d*\s*(m|cm|mm))', msg_lower)
    if size_match:
        result["entities"]["size"] = size_match.group(1)

    return result