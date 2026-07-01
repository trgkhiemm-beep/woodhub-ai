from pydantic import BaseModel, Field

class Location(BaseModel):
    lat: float
    lng: float

class MatchingRequest(BaseModel):
    required_capabilities: list[str] = Field(default_factory=list)
    customer_location: Location | None = None
    max_distance_km: float = 50
    workshops: list[dict] | None = None
    limit: int = 5