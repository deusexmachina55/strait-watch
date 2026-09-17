"""Small geometry helpers and the named areas used by collectors and indicators."""
import math

TAIWAN = (23.7, 121.0)
# Named circles (lat, lon, radius km) for AIS/ADS-B sightings and zone proximity
CIRCLES = {
    "kinmen": (24.45, 118.35, 30),
    "matsu": (26.15, 119.95, 30),
    "kaohsiung": (22.60, 120.27, 25),
    "keelung": (25.15, 121.75, 25),
    "taichung": (24.28, 120.50, 25),
    "mailiao": (23.80, 120.17, 25),
}
PORTS = ("kaohsiung", "keelung", "taichung", "mailiao")
STRAIT_BOX = (22.0, 26.5, 117.5, 121.0)  # lat_min, lat_max, lon_min, lon_max


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def in_box(lat: float, lon: float, box=STRAIT_BOX) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def in_circle(lat: float, lon: float, name: str) -> bool:
    clat, clon, r = CIRCLES[name]
    return haversine_km(lat, lon, clat, clon) <= r


def zones_for(lat: float, lon: float) -> list[str]:
    """Named areas a point falls in: kinmen, matsu, taiwan_port, strait."""
    out = [n for n in ("kinmen", "matsu") if in_circle(lat, lon, n)]
    if any(in_circle(lat, lon, p) for p in PORTS):
        out.append("taiwan_port")
    if in_box(lat, lon):
        out.append("strait")
    return out


def polygon_area_km2(points: list[tuple[float, float]]) -> float:
    """Shoelace on an equirectangular projection, fine for zones a few tens of km across."""
    if len(points) < 3:
        return 0.0
    lat0 = sum(p[0] for p in points) / len(points)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    xy = [((p[1]) * kx, p[0] * ky) for p in points]
    area = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
