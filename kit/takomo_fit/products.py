"""Load Takomo's product data (unmodified copy of takomo_products.json)."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("data") / "takomo_products.json"
HEAD_ORDER = ["101-mkii", "201-mkii", "201t-mkii", "301-cb", "301-mb"]
FLEX_ORDER = ["lady", "senior", "regular", "stiff", "x-stiff"]
GRIP_ORDER = ["standard", "midsize", "oversize"]
GLOVE_ORDER = ["XS", "S", "M", "M/L", "L", "XL"]
# Hand length (wrist crease to middle fingertip, cm) -> glove size. The M|M/L and
# L|XL edges are set so the simulated population lands on Takomo's real grip mix
# (standard 35 %, midsize 52 %, oversize 13 %, Jonas Koisti, 5 Nov 2026).
GLOVE_EDGES_CM = [16.8, 17.8, 18.59, 19.4, 20.22]


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    p = Path(path or os.environ.get("TAKOMO_PRODUCTS", DEFAULT_PATH))
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data["_heads"] = {h["slug"]: h for h in data["heads"]}
    data["_shafts"] = {s["slug"]: s for s in data["shafts"]}
    return data


def head(slug: str) -> dict:
    return load()["_heads"][slug]


def shaft(slug: str) -> dict:
    return load()["_shafts"][slug]


def shafts_for_head(head_slug: str) -> list[dict]:
    return [s for s in load()["shafts"] if head_slug in s["heads"]]


def flex_index(flex: str) -> int:
    return FLEX_ORDER.index(flex)
