import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import ROOT, get_settings


@dataclass(frozen=True)
class Brand:
    id: str
    folder: Path
    profile: dict

    @property
    def name(self) -> str:
        return self.profile["company"]["name"]

    @property
    def company(self) -> dict:
        return self.profile["company"]

    @property
    def colors(self) -> dict:
        return self.profile["visual"]["colors"]

    @property
    def logo_color(self) -> Path:
        return self.folder / "logo" / "logo-main-original.png"

    @property
    def logo_white(self) -> Path:
        return self.folder / "logo" / "logo-white-original.png"

    @property
    def service_names(self) -> list[str]:
        return [s["name"] for s in self.profile["services"]]

    def service(self, name: str) -> dict | None:
        name = (name or "").lower()
        for s in self.profile["services"]:
            if s["name"].lower() == name:
                return s
        return None

    def context_text(self, all_services: bool = True, focus: str | None = None) -> str:
        """The brand kit written out as plain text for the AI's instructions.

        all_services=True lists every service in one line each (for choosing one);
        focus adds the selling points of that one service. Kept short: free AI plans count every word."""
        p = self.profile
        c = p["company"]
        lines = [
            f"COMPANY: {c['name']} ({c['long_name']}), {c['type']}.",
            f"LOCATION / MARKET: {c['location']}. {c['market']}.",
            f"POSITIONING: {c['positioning']}",
            f"TAGLINES: {' | '.join(c['taglines'])}",
            f"CONTACT: phone/WhatsApp {c['phone']} · website {c['website']} · email {c['email']}",
            f"CONTACT PERSON: {p['person']['name']}, {p['person']['role']}.",
        ]
        if p["person"].get("linkedin_summary"):
            lines.append(f"ABOUT THE PERSON: {p['person']['linkedin_summary']}")
        lines.append("AUDIENCE: " + "; ".join(p["audience"]))
        if all_services:
            lines.append("SERVICES (use these exact names):")
            lines += [f"- [{s['category']}] {s['name']}: {s['one_liner']}" for s in p["services"]]
        svc = self.service(focus) if focus else None
        if svc:
            lines.append(f"THIS POST'S SERVICE: {svc['name']}: {svc['one_liner']} "
                         f"Selling points: {', '.join(svc['selling_points'])}.")
        v = p["voice"]
        lines.append(f"VOICE: {', '.join(v['tone'])}. Language: {v['language']}.")
        lines += [f"- {r}" for r in v["style_rules"]]
        lines.append("NEVER: " + " ".join(v["avoid"]))
        cp = p["claims_policy"]
        lines.append("FACTS YOU MAY STATE: " + "; ".join(cp["safe_to_use"]) + ".")
        lines.append("DO NOT STATE (unconfirmed): " + "; ".join(cp["do_not_use_until_confirmed"]) + ".")
        lines.append("CALLS TO ACTION: " + " | ".join(p["cta_options"]))
        return "\n".join(lines)


@lru_cache
def load_brand(brand_id: str | None = None) -> Brand:
    brand_id = brand_id or get_settings().brand_id
    folder = ROOT / "brand" / brand_id
    profile = json.loads((folder / "profile.json").read_text(encoding="utf-8"))
    return Brand(id=brand_id, folder=folder, profile=profile)
