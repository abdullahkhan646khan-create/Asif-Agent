"""Make real sample posts with Gemini (no Groq needed) to check quality.

    .venv/bin/python scripts/make_samples.py                 # make every sample that has no picture yet (account A)
    .venv/bin/python scripts/make_samples.py cloud cctv      # (re)make only these
    .venv/bin/python scripts/make_samples.py --rerender      # redo the designs from the saved pictures, no Gemini
    .venv/bin/python scripts/make_samples.py --account B     # use account B
Finished posts go to sample-posts/, the raw Gemini pictures to sample-posts/raw-gemini/.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brand import load_brand  # noqa: E402
from app.composer import compose  # noqa: E402
from app.config import ROOT  # noqa: E402
from app.imagegen import pool  # noqa: E402
from app.writer import image_prompt  # noqa: E402

OUT = ROOT / "sample-posts"
RAW = OUT / "raw-gemini"

SAMPLES = {
    # --- new: the client's dark "AI-Driven Networks" poster style (sample 4) ---
    "ai-driven-networks": {
        "template": "skyline", "headline_top": "Accelerate your business with", "headline_highlight": "AI-Driven Networks",
        "body": "Smart, scalable network infrastructure for UAE businesses: faster connections, higher reliability "
                "and stronger security, designed and supported by certified engineers.",
        "benefits": ["Faster connections", "High reliability", "Stronger security"],
        "image_prompt": "Dubai skyline at night with Burj Khalifa under a starry deep-blue sky. In the middle of the "
                        "frame, glowing streams of blue and violet light flow like data from the city toward a modern "
                        "server rack on the right, where an engineer in a dark shirt connects glowing blue network "
                        "cables. Cinematic, high detail, the sky is dark and calm.",
    },
    "agentic-ai": {
        "template": "skyline", "headline_top": "Let AI agents run your", "headline_highlight": "Daily Operations",
        "body": "Agentic AI that plans, executes and connects your business systems, so your team saves hours "
                "every day and focuses on growth.",
        "benefits": ["Hours saved daily", "Fewer errors", "Systems connected"],
        "image_prompt": "Dubai Marina skyline at night under a starry deep-blue sky. In the middle, a businessman in a "
                        "suit stands at a large glass window seen from behind, looking at a glowing holographic "
                        "network of connected nodes and app-like shapes floating above the city (no readable text). "
                        "Blue, cyan and violet light, cinematic, premium.",
    },
    "mobile-apps": {
        "template": "wave", "headline_top": "Grow your sales with a", "headline_highlight": "Mobile App",
        "subheadline": "Custom iOS & Android apps that keep your customers coming back",
        "benefits": ["More repeat sales", "iOS & Android", "Fast & secure"],
        "image_prompt": "A happy young professional in a modern Dubai café holding a smartphone that shows a colorful "
                        "shopping app interface with product cards (no readable text), positioned in the right half "
                        "of the frame. Warm daylight mixed with cool blue accents, shallow depth of field, premium "
                        "lifestyle photography.",
    },
    "backup-recovery": {
        "template": "cards", "headline_top": "Never lose your", "headline_highlight": "Business Data",
        "subheadline": "Automatic backups and fast recovery that keep your business running",
        "benefits": ["Daily backups", "Fast recovery", "Ransomware defense"],
        "image_prompt": "An IT engineer in a smart shirt standing next to a modern storage server rack with glowing "
                        "blue drive lights, holding a tablet and checking it calmly. Subject centered, clean bright "
                        "server room, cool blue tones, shallow depth of field.",
    },
    # --- earlier samples, now solution + benefit focused and without button text ---
    "structured-cabling": {
        "template": "wave", "headline_top": "Structured", "headline_highlight": "Cabling",
        "subheadline": "Clean, standards-based cabling for offices, data centers and commercial buildings",
        "benefits": ["Future-proof design", "Labeled & tested", "Less downtime"],
        "image_prompt": "A modern server room in a Dubai office: a neatly dressed network rack with color-coded blue "
                        "and yellow patch cords, perfectly organized and labeled. An engineer in a white hard hat and "
                        "safety vest is checking a patch panel on the right side of the frame.",
    },
    "ai-voice-receptionist": {
        "template": "spotlight", "headline_top": "Never miss", "headline_highlight": "A Call",
        "subheadline": "An AI receptionist that answers 24/7 and books appointments for you",
        "benefits": ["Answers 24/7", "Books appointments", "More leads"],
        "image_prompt": "A premium modern office reception desk at night in Dubai with a sleek headset glowing with "
                        "soft cyan and violet light waves, Dubai skyline behind.",
    },
    "cyber-security": {
        "template": "spotlight", "headline_top": "Protect your business from", "headline_highlight": "Cyber Threats",
        "subheadline": "Security for your data, systems and network, built for UAE businesses",
        "benefits": ["Threat protection", "Secure data", "Business continuity"],
        "image_prompt": "A modern security operations center at night with analysts in front of large monitors.",
    },
    "cctv": {
        "template": "wave", "headline_top": "See everything,", "headline_highlight": "Stop Threats",
        "subheadline": "Smart IP & AHD CCTV that protects your offices, warehouses and shops",
        "benefits": ["24/7 monitoring", "View from phone", "HD evidence"],
        "image_prompt": "Dome and bullet security cameras on a modern Dubai building facade at dusk.",
    },
    "access-control": {
        "template": "frame", "headline_top": "Secure every", "headline_highlight": "Entrance",
        "subheadline": "Card, PIN and biometric entry with central control of every door",
        "benefits": ["Central control", "Audit trails", "Biometric entry"],
        "image_prompt": "A professional tapping an access card on a sleek reader next to a glass office door.",
    },
    "whatsapp-chatbot": {
        "template": "spotlight", "headline_top": "Reply to customers in", "headline_highlight": "Seconds",
        "subheadline": "A WhatsApp AI chatbot that answers, qualifies leads and takes orders 24/7",
        "benefits": ["Instant replies", "More qualified leads", "Works 24/7"],
        "image_prompt": "A hand holding a smartphone with abstract chat bubbles at night in Dubai.",
    },
    "cloud": {
        "template": "wave", "headline_top": "Move your business to the", "headline_highlight": "Cloud",
        "subheadline": "Secure migration, hosting and collaboration that grows with you",
        "benefits": ["Work from anywhere", "Lower IT costs", "Scales with you"],
        "image_prompt": "A long clean data center aisle with blue LED lights.",
    },
    "time-attendance": {
        "template": "cards", "headline_top": "Smart", "headline_highlight": "Attendance",
        "subheadline": "Live check-in, instant reports and one dashboard for all branches",
        "benefits": ["Live check-in", "Instant reports", "Mobile check-in"],
        "image_prompt": "An employee using a fingerprint attendance terminal at an office entrance.",
    },
    "web-development": {
        "template": "wave", "headline_top": "Websites that", "headline_highlight": "Sell",
        "subheadline": "Fast, secure business websites and online stores that turn visitors into customers",
        "benefits": ["More online sales", "Mobile friendly", "SEO ready"],
        "image_prompt": "A laptop and phone showing a modern e-commerce website on a clean desk.",
    },
    "network-solutions": {
        "template": "spotlight", "headline_top": "Fast, reliable", "headline_highlight": "Networks",
        "subheadline": "LAN, WAN and SD-WAN designed for speed, security and less downtime",
        "benefits": ["Faster speeds", "Less downtime", "Easy to manage"],
        "image_prompt": "Enterprise network switches with blue cables and streams of light rising.",
    },
}


def render(name: str, brief: dict, raw: bytes) -> None:
    (OUT / f"{name}.jpg").write_bytes(compose(brief["template"], raw, brief, load_brand()))


async def main():
    args = sys.argv[1:]
    slot = "A"
    if "--account" in args:
        slot = args[args.index("--account") + 1].upper()
        args = [a for a in args if a not in ("--account", slot, slot.lower())]
    RAW.mkdir(parents=True, exist_ok=True)

    if "--rerender" in args:
        for name, brief in SAMPLES.items():
            raw_file = RAW / f"{name}.png"
            if raw_file.exists():
                render(name, brief, raw_file.read_bytes())
                print(f"re-designed {name}")
        return

    names = args or [n for n in SAMPLES if not (RAW / f"{n}.png").exists()]
    brand = load_brand()
    ok = 0
    for i, name in enumerate(names, 1):
        brief = SAMPLES[name]
        started = time.monotonic()
        try:
            raw, _ = await pool.generate(image_prompt(brand, brief), only_slot=slot)
        except Exception as e:
            print(f"{i}. {name}: FAILED ({type(e).__name__}: {e})", flush=True)
            continue
        (RAW / f"{name}.png").write_bytes(raw)
        render(name, brief, raw)
        ok += 1
        print(f"{i}. {name}: OK in {time.monotonic() - started:.0f}s", flush=True)
        await asyncio.sleep(8)  # gentle pace, so the account isn't flagged
    print(f"Done: {ok}/{len(names)} posts in {OUT}", flush=True)
    for s in list(pool.clients):
        await pool._drop_client(s)


if __name__ == "__main__":
    asyncio.run(main())
