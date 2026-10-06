"""Render every post layout with a sample photo, so you can check the design without any API keys.

Run:  .venv/bin/python scripts/preview_templates.py [path/to/photo.jpg]
Output goes to data/previews/.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brand import load_brand  # noqa: E402
from app.composer import TEMPLATES, compose  # noqa: E402
from app.config import ROOT  # noqa: E402

photo = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "brand/amanasoft/samples/sample-3-fiber-patch-photo.jpg"
brand = load_brand()
content = {
    "headline_top": "Structured",
    "headline_highlight": "Cabling",
    "subheadline": "For offices, data centers and commercial buildings",
    "bullets": [
        "Future-proof Cat6A & fiber design",
        "TIA/EIA and ISO/IEC standards",
        "Clean, labeled, documented racks",
        "Site survey to final testing",
    ],
    "benefits": ["Future-proof design", "Labeled racks", "Tested to standards"],
    "body": "Clean, standards-based copper and fiber cabling that keeps your offices and data centers fast, "
            "organized and ready to grow.",
}
out = ROOT / "data" / "previews"
out.mkdir(parents=True, exist_ok=True)
for t in TEMPLATES:
    (out / f"{t}.jpg").write_bytes(compose(t, photo.read_bytes(), content, brand))
    print("wrote", out / f"{t}.jpg")
