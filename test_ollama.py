import ollama

# Model qwen2.5:7b mà bạn vừa tải về
try:
    response = ollama.chat(model='qwen2.5:7b', messages=[{'role': 'user', 'content': 'Chào WoodHub!'}])
    print("✅ Kết nối thành công!")
    print("🤖 AI trả lời:", response['message']['content'])
except Exception as e:
    print("❌ Lỗi kết nối:", e)