"""Where is my bus? Live bus positions for any route in England from the DfT Bus Open Data Service (SIRI-VM).

Python standard library only. Works with no key (downloads the open bulk feed, about 3 MB). Set a free
BODS_API_KEY (https://data.bus-data.dft.gov.uk/) and the server filters for you, which is faster.

    python bods_live.py --line 72 --operator FBRA                  # every bus on First Bradford's 72
    python bods_live.py --near 53.795,-1.752 --radius 1            # every bus within 1 km of a point
    python bods_live.py --line 72 --operator FBRA --json           # machine-readable rows
    python bods_live.py --late 72                                  # West Yorkshire lateness (is-my-bus-late)
    python bods_live.py --selftest                                 # offline check on the bundled sample
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import tarfile
import zipfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

FEED = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/"
BULK = "https://data.bus-data.dft.gov.uk/avl/download/bulk_archive"   # same data, no key, whole country
NS = {"s": "http://www.siri.org.uk/siri"}
HERE = Path(__file__).resolve().parent
# is-my-bus-late commits one small per-route CSV per hourly sample; one tarball fetch gets them all.
IMBL_TARBALL = "https://codeload.github.com/damonchandio/is-my-bus-late/tar.gz/refs/heads/main"
FIELDS = ["line", "operator", "destination", "aimed_departure", "position_age_min", "distance_km", "vehicle"]


def get(url: str, timeout: int = 60) -> bytes:
    # same fetch as is-my-bus-late's pipeline/fetch.py
    req = urllib.request.Request(url, headers={"User-Agent": "uk-bus-skill/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def norm_line(s: str) -> str:
    """'072' -> '72', ' X6 ' -> 'X6'. LineRef is often zero-padded while PublishedLineName is not."""
    s = (s or "").strip().upper()
    return s.lstrip("0") or s


def when(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def bbox(lat: float, lon: float, km: float) -> str:
    dlat = km / 111.0
    dlon = km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return f"{lon - dlon:.5f},{lat - dlat:.5f},{lon + dlon:.5f},{lat + dlat:.5f}"   # minLng,minLat,maxLng,maxLat


def parse(xml: bytes, now: datetime | None = None, near: tuple[float, float] | None = None) -> list[dict]:
    """SIRI-VM VehicleActivity -> one row per bus."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for va in ET.fromstring(xml).iterfind(".//s:VehicleActivity", NS):
        j = va.find("s:MonitoredVehicleJourney", NS)
        if j is None:
            continue
        t = lambda path: (j.findtext(path, default="", namespaces=NS) or "").strip()
        lat, lon = t("s:VehicleLocation/s:Latitude"), t("s:VehicleLocation/s:Longitude")
        seen = when(va.findtext("s:RecordedAtTime", namespaces=NS))
        aimed = when(t("s:OriginAimedDepartureTime"))
        rows.append({
            "line": t("s:PublishedLineName") or t("s:LineRef"),
            "line_ref": t("s:LineRef"),
            "operator": t("s:OperatorRef"),
            "destination": t("s:DestinationName").replace("_", " "),
            "aimed_departure": aimed.astimezone().strftime("%H:%M") if aimed else None,
            "position_age_min": round((now - seen).total_seconds() / 60, 1) if seen else None,
            "distance_km": round(haversine_km(near[0], near[1], float(lat), float(lon)), 2) if near and lat and lon else None,
            "vehicle": t("s:VehicleRef"),
            "lat": float(lat) if lat else None,
            "lon": float(lon) if lon else None,
        })
    return rows


def keep(rows: list[dict], line: str | None, radius: float | None, operator: str | None = None) -> list[dict]:
    if operator:
        rows = [r for r in rows if r["operator"].upper() == operator.upper()]
    if line:
        want = norm_line(line)
        rows = [r for r in rows if want in (norm_line(r["line"]), norm_line(r["line_ref"]))]
    if radius is not None:
        rows = [r for r in rows if r["distance_km"] is not None and r["distance_km"] <= radius]
    key = lambda r: (r["distance_km"] if r["distance_km"] is not None else 0, r["position_age_min"] or 0)
    return sorted(rows, key=key)


def fetch(key: str, line: str | None, operator: str | None, box: str | None) -> bytes:
    q = {"api_key": key}
    if operator:
        q["operatorRef"] = operator
    if box:
        q["boundingBox"] = box
    if line:
        q["lineRef"] = line
    xml = get(FEED + "?" + urllib.parse.urlencode(q))
    # The server matches LineRef exactly ("72" misses "072"); retry without it and filter on our side.
    if line and b"VehicleActivity" not in xml and (operator or box):
        del q["lineRef"]
        xml = get(FEED + "?" + urllib.parse.urlencode(q))
    return xml


def lateness(line: str | None, source: str | None = None) -> list[dict]:
    """Per-route lateness from is-my-bus-late's hourly run CSVs, aggregated exactly as its pipeline/report.py does
    (observation-weighted median delay; shares over 5 min late, early, on time). West Yorkshire only."""
    runs = []
    if source and Path(source).is_dir():
        for p in sorted(Path(source).glob("*.csv")):
            runs.append(p.read_text(encoding="utf-8"))
    else:
        with tarfile.open(fileobj=io.BytesIO(get(IMBL_TARBALL)), mode="r:gz") as tar:
            for m in tar.getmembers():   # names only, never extracted to disk
                if m.isfile() and "/data/runs/" in m.name and m.name.endswith(".csv"):
                    runs.append(tar.extractfile(m).read().decode("utf-8"))
    agg: dict[tuple[str, str], dict] = {}
    for text in runs:
        for r in csv.DictReader(io.StringIO(text)):
            if line and norm_line(r["route"]) != norm_line(line):
                continue
            a = agg.setdefault((r["route"], r["operator"]), dict(n=0, trips=0, w=0.0, over5=0, early=0, ontime=0, runs=0))
            n = int(r["n"])
            a["n"] += n; a["trips"] += int(r["trips"]); a["runs"] += 1
            a["w"] += float(r["run_median"]) * n
            a["over5"] += int(r["n_over5"]); a["early"] += int(r["n_early"]); a["ontime"] += int(r["n_ontime"])
    out = []
    for (route, op), a in agg.items():
        if not a["n"]:
            continue
        out.append({"route": route, "operator": op, "typical_minutes_late": round(a["w"] / a["n"] / 60, 1),
                    "pct_over_5_late": round(100 * a["over5"] / a["n"], 1), "pct_early": round(100 * a["early"] / a["n"], 1),
                    "pct_on_time": round(100 * a["ontime"] / a["n"], 1), "trips": a["trips"], "hourly_samples": a["runs"],
                    "ranked": a["trips"] >= 30})
    return sorted(out, key=lambda r: -r["trips"])


def table(rows: list[dict], cols: list[str]) -> str:
    cells = [[("" if r.get(c) is None else str(r[c])) for c in cols] for r in rows]
    w = [max([len(c)] + [len(x[i]) for x in cells]) for i, c in enumerate(cols)]
    fmt = lambda xs: "  ".join(x.ljust(w[i]) for i, x in enumerate(xs)).rstrip()
    return "\n".join([fmt(cols), fmt(["-" * n for n in w])] + [fmt(x) for x in cells])


def selftest() -> None:
    xml = (HERE / "sample_siri_vm.xml").read_bytes()
    now = datetime(2026, 11, 8, 20, 30, tzinfo=timezone.utc)
    rows = parse(xml, now=now, near=(53.7950, -1.7520))
    assert len(rows) == 4, rows
    r72 = keep(rows, "72", None)
    assert [r["vehicle"] for r in r72] == ["69355", "69402"], r72        # LineRef 072 and 72 both match "72"
    assert keep(rows, "072", None) == r72
    assert r72[0]["destination"] == "Bradford Interchange" and r72[0]["operator"] == "FBRA"
    assert r72[0]["position_age_min"] == 0.5 and r72[1]["position_age_min"] == 12.0
    near = keep(rows, None, 1.0)
    assert [r["vehicle"] for r in near] == ["69355", "33012"] and near[0]["distance_km"] < near[1]["distance_km"] <= 1.0
    assert [r["vehicle"] for r in keep(rows, None, None, "arbb")] == ["33012"]
    assert bbox(53.795, -1.752, 1.0).count(",") == 3
    runs = HERE.parent / "tests" / "runs"
    late = lateness("72", str(runs))
    assert len(late) == 1 and late[0]["trips"] == 40 and late[0]["ranked"], late
    # weighted median: (120*10 + 360*30) / 40 / 60 = 5.0 min; over 5 = 12/40
    assert late[0]["typical_minutes_late"] == 5.0 and late[0]["pct_over_5_late"] == 30.0, late
    print("selftest ok: 4 buses parsed, line 72/072 matched, radius filter, lateness aggregation")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live bus positions in England from DfT Bus Open Data (SIRI-VM).")
    ap.add_argument("--line", help="route as on the bus, e.g. 72 or X6 (zero padding is ignored)")
    ap.add_argument("--operator", help="national operator code, e.g. FBRA (First Bradford), YTSY, ARBB")
    ap.add_argument("--near", help="lat,lon to search around")
    ap.add_argument("--radius", type=float, default=1.0, help="km around --near (default 1)")
    ap.add_argument("--json", action="store_true", help="print JSON rows instead of a table")
    ap.add_argument("--late", metavar="LINE", help="West Yorkshire lateness for a route, from is-my-bus-late")
    ap.add_argument("--late-data", help="local is-my-bus-late data/runs folder instead of downloading")
    ap.add_argument("--selftest", action="store_true", help="offline test on the bundled sample, no key needed")
    a = ap.parse_args(argv)

    if a.selftest:
        selftest()
        return 0
    if a.late:
        rows = lateness(a.late, a.late_data)
        if a.json:
            print(json.dumps(rows, indent=1))
        else:
            print(table(rows, ["route", "operator", "typical_minutes_late", "pct_over_5_late", "pct_early",
                               "pct_on_time", "trips", "ranked"]) if rows else f"No measurements for route {a.late}.")
        return 0
    if not (a.line or a.operator or a.near):
        ap.error("give at least one of --line, --operator or --near (the unfiltered feed is every bus in England)")
    key = os.getenv("BODS_API_KEY")
    near = tuple(float(x) for x in a.near.split(",")) if a.near else None
    if key:
        xml = fetch(key, a.line, a.operator, bbox(near[0], near[1], a.radius) if near else None)
    else:
        # No key: the open bulk archive is all of England (about 3 MB zipped), filtered here instead of by the server.
        xml = zipfile.ZipFile(io.BytesIO(get(BULK, 120))).read("siri.xml")
    rows = keep(parse(xml, near=near), a.line, a.radius if near else None, a.operator)
    if a.json:
        print(json.dumps([{k: r[k] for k in FIELDS + ["lat", "lon"]} for r in rows], indent=1))
    else:
        print(table(rows, FIELDS) if rows else "No buses found. Check the operator code, or the line may not be running now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
