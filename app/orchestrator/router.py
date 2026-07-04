# app/orchestrator/router.py
from app.orchestrator.analyzer import analyze_intent_and_entities
from app.rag.retriever import search_products
from app.services.gemini_service import gemini_service

class OrchestratorEngine:
    def __init__(self):
        self.llm = gemini_service
        
    def process_request(self, session_id: str, user_message: str) -> dict:
        # 1. Phân tích ngữ nghĩa
        analysis = analyze_intent_and_entities(user_message)
        intent = analysis["intent"]
        entities = analysis["entities"]
        
        # 2. DECISION ENGINE & ROUTING
        if intent == "SEARCH_PRODUCT":
            # GỌI PRODUCT AI: RAG -> Prompt Builder -> LLM
            # Lấy top 3 sản phẩm từ FAISS
            rag_docs = search_products(user_message, top_k=3) 
            rag_context = "\n".join([doc.get("content") for doc in rag_docs])
            
            # Khách hỏi tìm kiếm, ta nhét RAG Context vào
            system_prompt = f"DỮ LIỆU CATALOG: {rag_context}\nHãy tư vấn theo dữ liệu trên."
            
            ai_reply = self.llm.generate_response(session_id, user_message, system_prompt)
            return {"intent": intent, "answer": ai_reply, "entities": entities}

        elif intent == "DESIGN_3D":
            # GỌI DESIGN AI & PRICE PREDICTION AI
            return self._handle_design_and_pricing(user_message)
            
        elif intent == "CART_ACTION":
            # LLM tự động sử dụng function calling trong product_tools.py
            ai_reply = self.llm.generate_response(session_id, user_message)
            return {"intent": intent, "answer": ai_reply}
            
        else:
            # GỌI KNOWLEDGE AI (Hỏi đáp chung)
            ai_reply = self.llm.generate_response(session_id, user_message)
            return {"intent": intent, "answer": ai_reply}
            
    def _handle_design_and_pricing(self, user_message: str):
        """
        Price Prediction AI & Recommendation AI:
        Sử dụng HuggingFace Model để so khớp không gian Vector
        """
        # Giả lập gọi API Meshy (Trong thực tế em sẽ call httpx tới Meshy ở đây)
        meshy_status = "Đang khởi tạo model 3D..."
        
        # TIÊN ĐOÁN GIÁ (PRICE PREDICTION):
        # Thay vì dùng ML Regression phức tạp, ta dùng Vector Similarity (KNN)
        # Tìm 1 sản phẩm thật trong DB có thông số gần nhất với thiết kế 3D của khách
        closest_match = search_products(user_message, top_k=1)
        
        if closest_match:
            matched_product = closest_match[0]["metadata"]
            price_estimation = matched_product["price"]
            recommendation = matched_product["name"]
            
            ai_reply = (f"Hệ thống đang dựng thiết kế 3D cho bạn. Dựa trên dữ liệu xưởng, "
                        f"mẫu thiết kế của bạn tương đồng nhất với sản phẩm '{recommendation}'. "
                        f"Giá dự kiến (Price Prediction) để gia công khoảng: {price_estimation:,} VNĐ.")
        else:
            ai_reply = "Đang dựng bản 3D. Tuy nhiên xưởng chưa có mẫu tương đồng để báo giá chính xác."
            
        return {"intent": "DESIGN_3D", "answer": ai_reply, "status": meshy_status}

orchestrator = OrchestratorEngine()