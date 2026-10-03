"""Test the Gemini cookies for real: log in, make pictures, and turn one into a finished Amanasoft post.

Put the cookies in .env (GEMINI_A_1PSID / GEMINI_A_1PSIDTS, and B if you have it), then run:
    .venv/bin/python scripts/check_gemini.py          # tests every account that has a cookie
    .venv/bin/python scripts/check_gemini.py A        # only account A
Results are saved in data/gemini-test/.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brand import load_brand  # noqa: E402
from app.composer import compose  # noqa: E402
from app.config import ROOT  # noqa: E402
from app.imagegen import SLOTS, pool  # noqa: E402
from app.writer import image_prompt  # noqa: E402

OUT = ROOT / "data" / "gemini-test"

# Two realistic posts, written by hand so this test needs no Groq key.
BRIEFS = [
    {
        "template": "wave", "service": "Structured Cabling",
        "headline_top": "Structured", "headline_highlight": "Cabling",
        "subheadline": "For offices, data centers and commercial buildings", "bullets": [],
        "cta": "Get a free site survey",
        "image_prompt": "A modern server room in a Dubai office: a neatly dressed network rack with color-coded blue "
                        "and yellow patch cords, perfectly organized and labeled. An engineer in a white hard hat and "
                        "safety vest is checking a patch panel on the right side of the frame. Clean cable trays above, "
                        "glossy floor, cool blue and violet lighting with small warm yellow LED accents.",
    },
    {
        "template": "spotlight", "service": "AI Voice Receptionist",
        "headline_top": "Never miss", "headline_highlight": "A Call",
        "subheadline": "An AI receptionist that answers 24/7 and books appointments for you", "bullets": [],
        "cta": "Book a free demo",
        "image_prompt": "A premium modern office reception desk at night in Dubai, empty and calm, with a sleek "
                        "headset on the desk glowing with soft cyan and violet light waves rising from it like a "
                        "voice. Large windows behind show the Dubai skyline with city lights. The top third of the "
                        "image is dark night sky, the desk and headset are in the lower middle.",
    },
]


async def check_slot(slot: str) -> None:
    brand = load_brand()
    print(f"\n=== Account {slot} ===")
    st = await pool.check(slot)
    if st["state"] != "ok":
        print(f"  Login: FAILED ({st['state']}) {st.get('error') or ''}")
        return
    print("  Login: OK")
    for i, brief in enumerate(BRIEFS, 1):
        started = time.monotonic()
        try:
            raw, _ = await pool.generate(image_prompt(brand, brief), only_slot=slot)
        except Exception as e:
            print(f"  Image {i}: FAILED after {time.monotonic() - started:.0f}s: {type(e).__name__}: {e}")
            continue
        raw_path = OUT / f"{slot}-{i}-gemini.png"
        post_path = OUT / f"{slot}-{i}-post.jpg"
        raw_path.write_bytes(raw)
        post_path.write_bytes(compose(brief["template"], raw, brief, brand))
        print(f"  Image {i}: OK in {time.monotonic() - started:.0f}s → {post_path.relative_to(ROOT)}")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    slots = [a.upper() for a in sys.argv[1:]] or list(SLOTS)
    for slot in slots:
        psid, _ = await pool.cookies(slot)
        if not psid:
            print(f"\n=== Account {slot} === no cookie in .env, skipped")
            continue
        await check_slot(slot)
    for slot in list(pool.clients):
        await pool._drop_client(slot)


if __name__ == "__main__":
    asyncio.run(main())
