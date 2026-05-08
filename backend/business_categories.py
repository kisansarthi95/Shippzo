"""
Phase G — "What do you sell?" onboarding business categories.

Single source of truth for the 16 standardised business categories
new users pick during signup. The frontend onboarding screen reads
this list and validates POSTed values against it; analytics
aggregations (top sellers / fastest-growing categories / shipment
volume) join on the slug so renaming a category here is a careful
migration, not a per-row update.

Slug = stable lowercase identifier persisted to users.primary_business_category.
Label = display string shown in the UI.
Icon  = emoji shown on the selection card.
"""
from typing import List, Dict, Set


BUSINESS_CATEGORIES: List[Dict[str, str]] = [
    {"slug": "fashion_apparel",        "label": "Fashion & Apparel",        "icon": "👕"},
    {"slug": "beauty_personal_care",   "label": "Beauty & Personal Care",   "icon": "💄"},
    {"slug": "electronics_gadgets",    "label": "Electronics & Gadgets",    "icon": "📱"},
    {"slug": "grocery_food",           "label": "Grocery & Food",           "icon": "🛒"},
    {"slug": "organic_herbal",         "label": "Organic & Herbal Products","icon": "🌿"},
    {"slug": "home_kitchen",           "label": "Home & Kitchen",           "icon": "🏠"},
    {"slug": "handmade_crafts",        "label": "Handmade & Crafts",        "icon": "🎨"},
    {"slug": "jewellery_accessories",  "label": "Jewellery & Accessories",  "icon": "💎"},
    {"slug": "books_stationery",       "label": "Books & Stationery",       "icon": "📚"},
    {"slug": "toys_baby",              "label": "Toys & Baby Products",     "icon": "🧸"},
    {"slug": "health_fitness",         "label": "Health & Fitness",         "icon": "💪"},
    {"slug": "agriculture_farming",    "label": "Agriculture & Farming",    "icon": "🌾"},
    {"slug": "pet_supplies",           "label": "Pet Supplies",             "icon": "🐾"},
    {"slug": "automotive",             "label": "Automotive",               "icon": "🚗"},
    {"slug": "industrial_b2b",         "label": "Industrial & B2B",         "icon": "🏭"},
    {"slug": "other",                  "label": "Other",                    "icon": "✨"},
]

VALID_SLUGS: Set[str] = {c["slug"] for c in BUSINESS_CATEGORIES}


def is_valid_category(slug: str) -> bool:
    return isinstance(slug, str) and slug in VALID_SLUGS
