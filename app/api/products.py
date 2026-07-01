from fastapi import APIRouter

router = APIRouter()

PRODUCTS = [
    {
        "id": 1,
        "name": "Bàn học gỗ sồi",
        "price": 2500000,
    }
]

@router.get("/api/products")
def get_products():
    return PRODUCTS