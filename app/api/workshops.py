from fastapi import APIRouter
import httpx
from app.models.matching import MatchingRequest
from app.services.matching_service import match_workshops
from app.core.config import settings

router = APIRouter()

# Dữ liệu Mock tạm thời giữ lại ở tầng API phục vụ giai đoạn đầu
WORKSHOPS = [
    {"id": 1, "name": "Xưởng Mộc An Phát", "capabilities": ["cắt CNC", "sơn PU", "lắp ráp"], "distance_km": 4.5, "rating": 4.8},
    {"id": 2, "name": "WoodHub Workshop Bình Tân", "capabilities": ["thiết kế 3D", "cắt CNC", "dán veneer"], "distance_km": 9.2, "rating": 4.6},
    {"id": 3, "name": "Xưởng Nội Thất Minh Khang", "capabilities": ["lắp ráp", "sơn PU", "đóng gói"], "distance_km": 13.0, "rating": 4.2},
    {"id": 4, "name": "Gia Công Gỗ Sài Gòn", "capabilities": ["cắt CNC", "chà nhám", "sơn dầu"], "distance_km": 2.8, "rating": 4.0},
    {"id": 5, "name": "Xưởng Decor Tân Phú", "capabilities": ["thiết kế 3D", "sơn PU", "lắp ráp"], "distance_km": 7.0, "rating": 4.7},
    {"id": 6, "name": "Mộc Gia Nguyễn", "capabilities": ["đóng gói", "dán veneer", "lắp ráp"], "distance_km": 18.5, "rating": 4.9},
]

def fetch_workshops_from_backend():
    if not settings.WORKSHOP_API_URL:
        return WORKSHOPS
    response = httpx.get(settings.WORKSHOP_API_URL, timeout=5.0)
    response.raise_for_status()
    return response.json()

@router.get("/api/workshops")
def get_workshops():
    return WORKSHOPS

@router.post("/matching")
def matching_endpoint(request: MatchingRequest):
    workshops = request.workshops if request.workshops is not None else fetch_workshops_from_backend()
    limit = min(max(request.limit, 1), 5)
    
    customer_location = (
        {"lat": request.customer_location.lat, "lng": request.customer_location.lng}
        if request.customer_location
        else None
    )
    
    matches = match_workshops(
        workshops=workshops,
        required_capabilities=request.required_capabilities,
        customer_location=customer_location,
        max_distance_km=request.max_distance_km,
        limit=limit,
    )
    return {"matches": matches}