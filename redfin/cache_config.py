"""Cache TTL per /redfin/* endpoint (cache.py, keyed on the validated
params). Listings move by the hour (price drops, pendings, new listings),
so the search pages are short; a property's own record changes with its
listing events, so the sections are cached for a few hours; the public
record / school / climate / permit / zoning layers move on monthly feeds."""
from datetime import timedelta

# location-autocomplete rows: region ids never move, new addresses are rare.
LOCATIONS_CACHE = timedelta(days=7)
# region id -> name / breadcrumbs (and free text / ZIP -> region id).
REGION_CACHE = timedelta(days=7)
# for-sale / sold / rent / coordinate / URL search pages.
SEARCH_CACHE = timedelta(minutes=30)
# property link / address / id -> (propertyId, listingId): a listing id changes
# only when the home is re-listed.
PROPERTY_REF_CACHE = timedelta(hours=12)
# the merged property record (status, price, remarks, photos, history).
PROPERTY_CACHE = timedelta(hours=3)
# Redfin Estimate + value history + comparables: refreshed by the site daily.
ESTIMATE_CACHE = timedelta(hours=12)
# schools, climate risk, permits + zoning: monthly feeds.
PROPERTY_FACTS_CACHE = timedelta(days=3)
# rental building: unit availability and rents move daily.
RENTAL_CACHE = timedelta(hours=6)
# agent directory and profiles: deal counts move weekly.
AGENTS_CACHE = timedelta(hours=24)
AGENT_REVIEWS_CACHE = timedelta(hours=24)
# housing-market pages: the site aggregates monthly.
MARKET_CACHE = timedelta(hours=24)
