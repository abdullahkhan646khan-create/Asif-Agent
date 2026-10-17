"""Industries for demo posts: show a prospect what their social media could look like.

Each industry has a few scene ideas; demos for the same industry take the next scene, so they don't repeat.
"""

INDUSTRIES = {
    "limousine": ("Luxury Limousine & Chauffeur", "🚘", [
        "a gleaming black Mercedes-Benz S-Class and a Rolls-Royce at the entrance of a five-star hotel in Downtown Dubai "
        "at golden hour, a chauffeur in a dark suit holding the rear door open",
        "a luxury black sedan driving along Sheikh Zayed Road at night, the Burj Khalifa glowing behind it, light trails",
        "the elegant leather rear cabin of a luxury limousine with soft ambient lighting and Dubai Marina through the window",
    ]),
    "realestate": ("Real Estate", "🏙️", [
        "a modern luxury villa with an infinity pool on Palm Jumeirah at sunset, warm interior lights glowing",
        "a bright, elegant apartment living room with floor-to-ceiling windows looking at the Burj Khalifa",
        "a smiling agent handing house keys to a happy young family in front of a new townhouse in Dubai Hills",
    ]),
    "restaurant": ("Restaurants & Cafés", "🍽️", [
        "a beautifully plated gourmet dish on a marble table in an upscale Dubai restaurant, soft warm light",
        "a cozy specialty café with a barista pouring latte art, morning light, plants and wooden details",
        "a rooftop restaurant terrace at dusk with set tables, candles and the Dubai skyline behind",
    ]),
    "hotel": ("Hotels & Hospitality", "🏨", [
        "a grand luxury hotel lobby with a sparkling chandelier, marble floors and a welcoming concierge",
        "an elegant hotel suite with a king bed, crisp white linen and a sea view of the Arabian Gulf",
        "a resort infinity pool with sun loungers and palm trees overlooking Jumeirah Beach at golden hour",
    ]),
    "clinic": ("Healthcare & Clinics", "🩺", [
        "a calm, modern clinic reception with friendly staff, soft daylight and clean white and teal tones",
        "a caring doctor talking with a patient in a bright, modern consultation room",
        "a dentist with a smiling patient in a modern dental clinic with the latest equipment",
    ]),
    "retail": ("Retail & Fashion", "🛍️", [
        "a stylish fashion boutique in Dubai Mall with elegant clothes on rails and warm spotlights",
        "a happy shopper holding branded-free shopping bags walking through a luxury mall corridor",
        "a close-up of luxury accessories, a watch and a handbag, displayed on a marble counter",
    ]),
    "automotive": ("Car Dealers & Rentals", "🚗", [
        "a sleek new sports car in a bright, modern showroom with glossy floors and dramatic lighting",
        "a customer receiving car keys from a smiling advisor in a premium car dealership",
        "a convertible driving on an open desert road near Dubai at sunset, dunes glowing orange",
    ]),
    "beauty": ("Beauty Salons & Spas", "💆", [
        "a serene spa treatment room with candles, folded towels, orchids and soft warm light",
        "a modern beauty salon with a stylist finishing a client's hair, elegant mirrors and lights",
        "a close-up of luxury skincare products on a stone surface with water drops and soft shadows",
    ]),
    "fitness": ("Gyms & Fitness", "🏋️", [
        "a modern premium gym with rows of equipment, large windows and a Dubai skyline view at sunrise",
        "a personal trainer coaching a motivated client lifting weights, dynamic light",
        "an outdoor group fitness class on a Dubai beach at sunrise, energetic and healthy",
    ]),
    "construction": ("Construction & Engineering", "🏗️", [
        "engineers in white safety helmets reviewing plans on a tablet at a large building site with cranes at sunrise",
        "a modern high-rise tower under construction in Dubai with cranes against a blue sky",
        "an aerial view of a large infrastructure project with heavy machinery working at golden hour",
    ]),
}


def industry_name(key: str) -> str:
    return INDUSTRIES[key][0] if key in INDUSTRIES else key


def next_scene(key: str, used_before: int) -> str:
    scenes = INDUSTRIES[key][2]
    return scenes[used_before % len(scenes)]
