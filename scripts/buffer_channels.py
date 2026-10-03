"""List the channels connected to your Buffer account.

Run:  .venv/bin/python scripts/buffer_channels.py
Copy IDs into BUFFER_CHANNEL_IDS only if you want to limit posting to specific channels.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.publisher import SERVICES, list_channels  # noqa: E402


async def main():
    channels = await list_channels()
    if not channels:
        print("No channels found. Connect Facebook, X and Threads in Buffer first.")
    for c in channels:
        used = "will post" if c["service"] in SERVICES else "ignored"
        flags = " (DISCONNECTED)" if c.get("isDisconnected") else ""
        print(f"{c['id']}  {c['service']:<10} {c['name']:<30} [{used}]{flags}")


asyncio.run(main())
