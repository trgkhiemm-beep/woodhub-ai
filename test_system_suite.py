import asyncio
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

from app.core.config import settings
from app.services.classifier import classifier
from app.services.business_engine import business_engine
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

async def run_tests():
    print("=" * 60)
    print("STARTING WOODHUB CHATBOT COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    passed_count = 0
    total_count = 12

    # --- TEST 1: Tôi muốn mua bàn gỗ. -> IN_SCOPE ---
    res1 = await classifier.classify("Tôi muốn mua bàn gỗ.")
    print(f"\n[TEST 1] Query: 'Tôi muốn mua bàn gỗ.'")
    print(f"Result scope: {res1.get('scope')}")
    if res1.get('scope') == "IN_SCOPE":
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 2: toi muon mua ban go -> IN_SCOPE ---
    res2 = await classifier.classify("toi muon mua ban go")
    print(f"\n[TEST 2] Query: 'toi muon mua ban go'")
    print(f"Result scope: {res2.get('scope')}")
    if res2.get('scope') == "IN_SCOPE":
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 3: BAN GO PHONG KHACH!!! -> IN_SCOPE ---
    res3 = await classifier.classify("BAN GO PHONG KHACH!!!")
    print(f"\n[TEST 3] Query: 'BAN GO PHONG KHACH!!!'")
    print(f"Result scope: {res3.get('scope')}")
    if res3.get('scope') == "IN_SCOPE":
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 4: Bitcoin hôm nay thế nào? -> OUT_OF_SCOPE ---
    res4 = client.post("/chat", json={"query": "Bitcoin hôm nay thế nào?", "session_id": "t4"})
    print(f"\n[TEST 4] Query: 'Bitcoin hôm nay thế nào?'")
    print(f"Response body: {res4.text}")
    if settings.OUT_OF_SCOPE_MESSAGE in res4.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 5: Thời tiết hôm nay? -> OUT_OF_SCOPE ---
    res5 = client.post("/chat", json={"query": "Thời tiết hôm nay?", "session_id": "t5"})
    print(f"\n[TEST 5] Query: 'Thời tiết hôm nay?'")
    print(f"Response body: {res5.text}")
    if settings.OUT_OF_SCOPE_MESSAGE in res5.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 6: cái này giá bao nhiêu (no context) -> AMBIGUOUS ---
    res6 = client.post("/chat", json={"query": "cái này giá bao nhiêu", "session_id": "t6_no_ctx"})
    print(f"\n[TEST 6] Query: 'cái này giá bao nhiêu'")
    print(f"Response body: {res6.text}")
    if settings.AMBIGUOUS_CLARIFICATION_MESSAGE in res6.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 7: xyz abc 123 -> UNKNOWN ---
    res7 = client.post("/chat", json={"query": "xyz abc 123", "session_id": "t7"})
    print(f"\n[TEST 7] Query: 'xyz abc 123'")
    print(f"Response body: {res7.text}")
    if settings.UNKNOWN_MESSAGE in res7.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 8: Product exists in DB -> Answer from DB ---
    res8 = client.post("/chat", json={"query": "Cho tôi xem bàn ăn", "session_id": "t8"})
    print(f"\n[TEST 8] Query: 'Cho tôi xem bàn ăn'")
    print(f"Response status: {res8.status_code}")
    if res8.status_code == 200 and "data:" in res8.text and "MISSING" not in res8.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 9: Product does not exist -> EXACTLY MISSING_PRODUCT_MESSAGE ---
    res9 = client.post("/chat", json={"query": "Tôi muốn mua giường ngủ siêu cấp vũ trụ 100m", "session_id": "t9"})
    print(f"\n[TEST 9] Query: 'Tôi muốn mua giường ngủ siêu cấp vũ trụ 100m'")
    print(f"Response body: {res9.text}")
    if settings.MISSING_PRODUCT_MESSAGE in res9.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 10: Product attribute missing -> ATTRIBUTE_NOT_FOUND_MESSAGE ---
    # Create product mock with missing requested attribute
    fake_product = {"name": "Bàn ABC", "product_variants": [{"price": 1000, "color": None}]}
    has_attr = business_engine.check_attribute_availability(fake_product, "color")
    print(f"\n[TEST 10] Checking attribute 'color' on product with color=None")
    print(f"Attribute available: {has_attr}")
    if not has_attr:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 11: Database connection failure -> DATABASE_ERROR_MESSAGE ---
    # Temporarily set supabase client to None to simulate error
    original_supa = business_engine.supabase
    business_engine.supabase = None
    res11 = client.post("/chat", json={"query": "Bàn học sinh gỗ thông", "session_id": "t11"})
    business_engine.supabase = original_supa
    print(f"\n[TEST 11] Simulated DB failure query")
    print(f"Response body: {res11.text}")
    if settings.DATABASE_ERROR_MESSAGE in res11.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    # --- TEST 12: Unrelated question with different wording -> OUT_OF_SCOPE ---
    res12 = client.post("/chat", json={"query": "Ai là tổng thống thứ 47 của nước Mỹ?", "session_id": "t12"})
    print(f"\n[TEST 12] Query: 'Ai là tổng thống thứ 47 của nước Mỹ?'")
    print(f"Response body: {res12.text}")
    if settings.OUT_OF_SCOPE_MESSAGE in res12.text:
        print("=> PASS")
        passed_count += 1
    else:
        print("=> FAIL")

    print("\n" + "=" * 60)
    print(f"SUMMARY RESULT: {passed_count}/{total_count} TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
