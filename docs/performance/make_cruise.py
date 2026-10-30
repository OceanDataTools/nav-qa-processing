"""A synthetic OpenRVDAS cruise for docs/performance.md: DAYS days of GGA at RATE Hz (nav33: an ISO
timestamp, a comma, then the sentence), one file a day, from a ship steaming in a slow circle off
San Diego from 2026-07-01.

usage: make_cruise.py OUTPUT_DIR DAYS RATE      e.g. make_cruise.py cruise 20 2: 3,456,000 fixes
"""

import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def checksum(body: str) -> str:
    value = 0
    for character in body:
        value ^= ord(character)
    return f"{value:02X}"


def minutes(degrees: float, width: int) -> str:
    """Degrees as NMEA's dddmm.mmmmmmm"""
    return f"{int(degrees):0{width}d}{(degrees % 1) * 60:010.7f}"


output, days, rate = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
output.mkdir(parents=True, exist_ok=True)
start = datetime(2026, 7, 1, tzinfo=UTC)
step = timedelta(seconds=1 / rate)
for day in range(days):
    moment = start + timedelta(days=day)
    with open(output / f"POSMV_GGA-{moment:%Y-%m-%d}.txt", "w") as file:
        for i in range(86_400 * rate):
            seconds = day * 86_400 + i / rate
            lat = 32.6 + 0.5 * math.sin(seconds / 200_000)
            lon = -118.0 + 0.5 * math.cos(seconds / 200_000)
            body = (
                f"GPGGA,{moment:%H%M%S}.{moment.microsecond // 10_000:02d},"
                f"{minutes(abs(lat), 2)},N,{minutes(abs(lon), 3)},W,2,12,0.8,12.3,M,-34.1,M,,"
            )
            file.write(f"{moment:%Y-%m-%dT%H:%M:%S.%f}Z,${body}*{checksum(body)}\n")
            moment += step
