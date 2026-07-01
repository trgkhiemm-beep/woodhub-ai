import httpx
import json

def test():
    try:
        response = httpx.get("http://localhost:8000/")
        with open("test_out.txt", "w", encoding="utf-8") as f:
            f.write(f"Root Endpoint: {response.status_code} {response.text}\n")
        
        payload = {
            "session_id": "test_session_123",
            "user_prompt": "xin chào, tôi muốn xem bàn làm việc"
        }
        
        response = httpx.post("http://localhost:8000/chat", json=payload, timeout=30.0)
        with open("test_out.txt", "a", encoding="utf-8") as f:
            f.write(f"Chat Endpoint: {response.status_code}\n")
            f.write(json.dumps(response.json(), indent=2, ensure_ascii=False))
            
    except Exception as e:
        with open("test_out.txt", "a", encoding="utf-8") as f:
            f.write(f"Error: {e}\n")

if __name__ == "__main__":
    test()
