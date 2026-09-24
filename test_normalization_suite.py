import asyncio
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from app.core.config import settings
from app.services.classifier import classifier
from app.services.input_normalizer import normalize_vietnamese_chat
from app.services.business_engine import business_engine
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

async def run_normalization_tests():
    print("=" * 70)
    print("RUNNING VIETNAMESE CHAT NORMALIZATION & ROBUST UNDERSTANDING TEST SUITE")
    print("=" * 70)

    test_results = []

    def check(name, condition, details=""):
        status = "PASS" if condition else "FAIL"
        test_results.append((name, status, details))
        print(f"[{status}] {name} {details}")

    # --- 1. KHÔNG DẤU ---
    print("\n--- NHÓM 1: TIẾNG VIỆT KHÔNG DẤU ---")
    c1 = await classifier.classify("co ban trang diem k")
    check("1.1 'co ban trang diem k' -> IN_SCOPE & PRODUCT_EXISTENCE", 
          c1.get("scope") == "IN_SCOPE" and c1.get("intent") == "PRODUCT_EXISTENCE",
          f"entities: {c1.get('entities')}")

    c2 = await classifier.classify("toi can ban go")
    check("1.2 'toi can ban go' -> IN_SCOPE & ban go",
          c2.get("scope") == "IN_SCOPE",
          f"entities: {c2.get('entities')}")

    c3 = await classifier.classify("ban go phong khach")
    check("1.3 'ban go phong khach' -> IN_SCOPE",
          c3.get("scope") == "IN_SCOPE")

    c4 = await classifier.classify("tu quan ao go")
    check("1.4 'tu quan ao go' -> IN_SCOPE",
          c4.get("scope") == "IN_SCOPE")


    # --- 2. SAI CHÍNH TẢ ---
    print("\n--- NHÓM 2: SAI CHÍNH TẢ (TYPO) ---")
    c5 = await classifier.classify("co ban trag diem k")
    check("2.1 'co ban trag diem k' -> IN_SCOPE (trag -> trang)",
          c5.get("scope") == "IN_SCOPE" and "trang diem" in c5.get("normalized_input", ""))

    c6 = await classifier.classify("ban trang diemm")
    check("2.2 'ban trang diemm' -> IN_SCOPE (diemm -> diem)",
          c6.get("scope") == "IN_SCOPE" and "trang diem" in c6.get("normalized_input", ""))

    c7 = await classifier.classify("tu quan aoo")
    check("2.3 'tu quan aoo' -> IN_SCOPE (aoo -> ao)",
          c7.get("scope") == "IN_SCOPE" and "quan ao" in c7.get("normalized_input", ""))


    # --- 3. KHÔNG DẤU + SAI CHÍNH TẢ ---
    print("\n--- NHÓM 3: KHÔNG DẤU + SAI CHÍNH TẢ ---")
    c8 = await classifier.classify("co bn trag diem k")
    check("3.1 'co bn trag diem k' -> IN_SCOPE (bn trag -> ban trang)",
          c8.get("scope") == "IN_SCOPE")

    c9 = await classifier.classify("toi can ban phong khahc")
    check("3.2 'toi can ban phong khahc' -> IN_SCOPE (khahc -> khach)",
          c9.get("scope") == "IN_SCOPE")

    c10 = await classifier.classify("co tu quan aoo k")
    check("3.3 'co tu quan aoo k' -> IN_SCOPE & PRODUCT_EXISTENCE",
          c10.get("scope") == "IN_SCOPE" and c10.get("intent") == "PRODUCT_EXISTENCE")


    # --- 4. TEENCODE & VIẾT TẮT ---
    print("\n--- NHÓM 4: TEENCODE & VIẾT TẮT ---")
    c11 = await classifier.classify("co ban trang diem ko")
    check("4.1 'co ban trang diem ko' -> IN_SCOPE", c11.get("scope") == "IN_SCOPE")

    c12 = await classifier.classify("co ban trang diem hok")
    check("4.2 'co ban trang diem hok' -> IN_SCOPE", c12.get("scope") == "IN_SCOPE")

    c13 = await classifier.classify("co sp ban go nao ko")
    check("4.3 'co sp ban go nao ko' -> IN_SCOPE", c13.get("scope") == "IN_SCOPE")

    c14 = await classifier.classify("co ban td k")
    check("4.4 'co ban td k' -> IN_SCOPE (td -> trang diem)",
          c14.get("scope") == "IN_SCOPE" and "trang diem" in c14.get("normalized_input", ""))

    c15 = await classifier.classify("co cai ban make up nao ko")
    check("4.5 'co cai ban make up nao ko' -> IN_SCOPE (make up -> trang diem)",
          c15.get("scope") == "IN_SCOPE" and "trang diem" in c15.get("normalized_input", ""))


    # --- 5. KHÔNG DẤU + TYPO + TEENCODE ĐỒNG THỜI ---
    print("\n--- NHÓM 5: KHÔNG DẤU + TYPO + TEENCODE ĐỒNG THỜI ---")
    c16 = await classifier.classify("co bn trag diem hok")
    check("5.1 'co bn trag diem hok' -> IN_SCOPE", c16.get("scope") == "IN_SCOPE")

    c17 = await classifier.classify("co sp ban go nao k")
    check("5.2 'co sp ban go nao k' -> IN_SCOPE", c17.get("scope") == "IN_SCOPE")

    c18 = await classifier.classify("co tu qao go ko")
    check("5.3 'co tu qao go ko' -> IN_SCOPE (qao -> quan ao)",
          c18.get("scope") == "IN_SCOPE" and "quan ao" in c18.get("normalized_input", ""))

    c19 = await classifier.classify("co bn trg diem k")
    check("5.4 'co bn trg diem k' -> IN_SCOPE (trg -> trang)",
          c19.get("scope") == "IN_SCOPE")


    # --- 6. KÉO DÀI KÝ TỰ ---
    print("\n--- NHÓM 6: KÉO DÀI KÝ TỰ ---")
    c20 = await classifier.classify("coooo ban go kkk")
    check("6.1 'coooo ban go kkk' -> IN_SCOPE (coooo -> co)",
          c20.get("scope") == "IN_SCOPE")

    c21 = await classifier.classify("co ban trang diemm kooo")
    check("6.2 'co ban trang diemm kooo' -> IN_SCOPE",
          c21.get("scope") == "IN_SCOPE")


    # --- 7. MIXED CASE ---
    print("\n--- NHÓM 7: MIXED CASE ---")
    c22 = await classifier.classify("CO BN TRAG DIEM K")
    check("7.1 'CO BN TRAG DIEM K' -> IN_SCOPE", c22.get("scope") == "IN_SCOPE")

    c23 = await classifier.classify("Co Ban Trag Diem Ko")
    check("7.2 'Co Ban Trag Diem Ko' -> IN_SCOPE", c23.get("scope") == "IN_SCOPE")


    # --- 8. NHIỀU CÁCH HIỂU (AMBIGUOUS KHÔNG CÓ CONTEXT) ---
    print("\n--- NHÓM 8: NHIỀU CÁCH HIỂU (AMBIGUOUS KHÔNG CÓ CONTEXT) ---")
    c24 = await classifier.classify("co ban dep k")
    check("8.1 'co ban dep k' -> AMBIGUOUS (bàn/bạn/bán đẹp)",
          c24.get("scope") == "AMBIGUOUS")

    c25 = await classifier.classify("ban nay")
    check("8.2 'ban nay' -> AMBIGUOUS (thiếu context)",
          c25.get("scope") == "AMBIGUOUS")

    c26 = await classifier.classify("co cai nay k")
    check("8.3 'co cai nay k' -> AMBIGUOUS",
          c26.get("scope") == "AMBIGUOUS")


    # --- 9. UNKNOWN / GIBBERISH ---
    print("\n--- NHÓM 9: UNKNOWN / GIBBERISH ---")
    c27 = await classifier.classify("asd qwe xyz")
    check("9.1 'asd qwe xyz' -> UNKNOWN", c27.get("scope") == "UNKNOWN")

    c28 = await classifier.classify("jshdjs 123")
    check("9.2 'jshdjs 123' -> UNKNOWN", c28.get("scope") == "UNKNOWN")


    # --- 10. END-TO-END CHAT API & DETERMINISTIC RESPONSE ---
    print("\n--- NHÓM 10: END-TO-END CHAT API & DETERMINISTIC RESPONSE ---")
    
    # 10.1 Deterministic existence check for 'co ban trang diem k'
    res_e2e_1 = client.post("/chat", json={"query": "co bn trag diem k", "session_id": "sess_norm_1"})
    check("10.1 E2E 'co bn trag diem k' -> Deterministic answer with Bàn Trang Điểm",
          "Có. Hệ thống hiện có" in res_e2e_1.text and "Bàn Trang Điểm" in res_e2e_1.text,
          f"resp: {res_e2e_1.text[:100]}...")

    # 10.2 Ambiguous without context -> AMBIGUOUS message
    res_e2e_2 = client.post("/chat", json={"query": "co ban dep k", "session_id": "sess_norm_amb"})
    check("10.2 E2E 'co ban dep k' -> AMBIGUOUS clarification",
          settings.AMBIGUOUS_CLARIFICATION_MESSAGE in res_e2e_2.text)

    # 10.3 Unknown gibberish -> UNKNOWN message
    res_e2e_3 = client.post("/chat", json={"query": "asd qwe xyz", "session_id": "sess_norm_unk"})
    check("10.3 E2E 'asd qwe xyz' -> UNKNOWN clarification",
          settings.UNKNOWN_MESSAGE in res_e2e_3.text)

    # 10.4 Context-aware reference: User first asks about bàn trang điểm, then asks 'bn nay co mau trang k'
    res_ctx_step1 = client.post("/chat", json={"query": "Tôi đang xem bàn trang điểm gỗ sồi.", "session_id": "sess_ctx_chain"})
    res_ctx_step2 = client.post("/chat", json={"query": "bn nay co mau trang k", "session_id": "sess_ctx_chain"})
    check("10.4 E2E Context Chain: 'bn nay co mau trang k' resolved with session context",
          res_ctx_step2.status_code == 200 and settings.AMBIGUOUS_CLARIFICATION_MESSAGE not in res_ctx_step2.text,
          f"status: {res_ctx_step2.status_code}")

    # Summary
    passed = sum(1 for _, s, _ in test_results if s == "PASS")
    total = len(test_results)
    print("\n" + "=" * 70)
    print(f"TEST RESULT: {passed}/{total} TESTS PASSED")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_normalization_tests())
