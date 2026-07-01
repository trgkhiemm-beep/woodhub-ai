from math import asin, cos, radians, sin, sqrt


def normalize_text(value: str) -> str:
    return value.strip().lower()


def capability_score(required_capabilities: list[str], workshop_capabilities: list[str]) -> float:
    if not required_capabilities:
        return 0.0

    required = {normalize_text(capability) for capability in required_capabilities}
    available = {normalize_text(capability) for capability in workshop_capabilities}
    matched = required.intersection(available)
    return len(matched) / len(required)


def haversine_km(origin: dict, destination: dict) -> float:
    origin_lat = radians(float(origin["lat"]))
    origin_lng = radians(float(origin["lng"]))
    destination_lat = radians(float(destination["lat"]))
    destination_lng = radians(float(destination["lng"]))

    lat_delta = destination_lat - origin_lat
    lng_delta = destination_lng - origin_lng
    a = sin(lat_delta / 2) ** 2 + cos(origin_lat) * cos(destination_lat) * sin(lng_delta / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def resolve_distance_km(workshop: dict, customer_location: dict | None) -> float | None:
    if workshop.get("distance_km") is not None:
        return float(workshop["distance_km"])

    if customer_location and workshop.get("location"):
        return haversine_km(customer_location, workshop["location"])

    return None


def distance_score(distance_km: float | None, max_distance_km: float) -> float:
    if distance_km is None:
        return 0.0

    if max_distance_km <= 0:
        return 1.0 if distance_km == 0 else 0.0

    return max(0.0, 1 - (distance_km / max_distance_km))


def rating_score(rating: float | int | None) -> float:
    if rating is None:
        return 0.0

    return min(max(float(rating), 0.0), 5.0) / 5


def score_workshop(
    workshop: dict,
    required_capabilities: list[str],
    customer_location: dict | None = None,
    max_distance_km: float = 50,
) -> dict:
    capability = capability_score(required_capabilities, workshop.get("capabilities", []))
    distance_km = resolve_distance_km(workshop, customer_location)
    distance = distance_score(distance_km, max_distance_km)
    rating = rating_score(workshop.get("rating"))
    score = capability + distance + rating

    return {
        **workshop,
        "distance_km": distance_km,
        "match": {
            "capability": round(capability, 4),
            "distance": round(distance, 4),
            "rating": round(rating, 4),
            "score": round(score, 4),
        },
    }


def match_workshops(
    workshops: list[dict],
    required_capabilities: list[str],
    customer_location: dict | None = None,
    max_distance_km: float = 50,
    limit: int = 5,
) -> list[dict]:
    scored_workshops = [
        score_workshop(
            workshop=workshop,
            required_capabilities=required_capabilities,
            customer_location=customer_location,
            max_distance_km=max_distance_km,
        )
        for workshop in workshops
    ]

    return sorted(
        scored_workshops,
        key=lambda workshop: workshop["match"]["score"],
        reverse=True,
    )[:limit]
