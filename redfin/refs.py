"""Reference resolvers + the code tables the /redfin/* schemas and parsers
share. Pure functions, no I/O.

ONE param per input (tripadvisor QueryOrIdField convention):

  location   a Redfin region id ("city/30818", "zipcode/78704",
             "neighborhood/165450", "county/2866", "school/135664",
             "school-district/5816", "state/36"), the same region's
             redfin.com link (with or without a /filter/... tail), or free
             text ("Austin, TX", "78704", "Allandale Austin") that the
             site's own autocomplete resolves
  property   a propertyId (31466150), a redfin.com home link
             (/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150) or a US
             street address
  rental     a propertyId or the rental building's redfin.com home link
  agent      an agent slug ("david-tom") or the profile / reviews link

Resolvers return a small dict describing WHAT was given; the network step
(api.py) turns free text, addresses and ZIP codes into Redfin ids through
the autocomplete, so the schema layer stays offline.

Region codes (captured 2026-10-30/31 from the site's own calls):
  gis `region_type`      1 neighborhood, 2 zipcode, 4 state, 5 county,
                         6 city, 7 school, 8 school district
  autocomplete row type  1 address, 2 city, 3 school, 4 zipcode, 5 county,
                         6 neighborhood, 10 agents directory, 11 state,
                         16 building, 23 no match, 27 school district
The two numberings differ, so a region is named here by its URL word
("city/30818"), never by a bare numeric type. A ZIP region's URL carries
the ZIP code (/zipcode/78704), not Redfin's table id (34098), so
"zipcode/<zip>" is resolved through the autocomplete like free text.
"""
import re
from urllib.parse import unquote, urlparse

SITE = "https://www.redfin.com"
HOST = "redfin.com"

# URL word -> gis region_type
REGION_TYPE_OF_WORD = {
    "neighborhood": 1, "zipcode": 2, "state": 4, "county": 5, "city": 6,
    "school": 7, "school-district": 8,
}
WORD_OF_REGION_TYPE = {v: k for k, v in REGION_TYPE_OF_WORD.items()}
# autocomplete row type -> gis region_type (regions only)
REGION_TYPE_OF_AUTOCOMPLETE = {"2": 6, "3": 7, "4": 2, "5": 5, "6": 1, "11": 4, "27": 8}
AUTOCOMPLETE_KIND = {
    "1": "address", "2": "city", "3": "school", "4": "zipcode", "5": "county",
    "6": "neighborhood", "10": "agents", "11": "state", "16": "building",
    "27": "school_district",
}

# gis `uipt` (property type) codes, sale side
PROPERTY_TYPES = {
    "house": 1, "condo": 2, "townhouse": 3, "multi_family": 4, "land": 5,
    "other": 6, "manufactured": 7, "co_op": 8,
}
PROPERTY_TYPE_NAMES = {
    1: "house", 2: "condo", 3: "townhouse", 4: "multi_family", 5: "land",
    6: "other", 7: "manufactured", 8: "co_op", 13: "townhouse",
}
# the `propertyType` of a home record (the detail payloads use this numbering)
HOME_PROPERTY_TYPE_NAMES = {
    1: "co_op", 2: "multi_family", 3: "condo", 4: "multi_family", 5: "apartment",
    6: "house", 7: "manufactured", 8: "other", 9: "other", 10: "vacant_land",
    11: "other", 12: "other", 13: "townhouse",
}
# rental `uipt` codes (the site's apartments-for-rent filter)
RENTAL_PROPERTY_TYPES = {"house": 1, "condo": 2, "townhouse": 3, "apartment": 4}

# gis `status` bitmasks (the site's own; competitor docs agree)
SALE_STATUS = {
    "active_and_coming_soon": 9, "active": 1, "coming_soon": 8,
    "pending": 130, "active_and_pending": 131,
}
SOLD_STATUS = 4
SOLD_WITHIN_DAYS = (7, 30, 90, 180, 365, 730, 1095, 1825)

SALE_SORTS = {
    "recommended": "redfin-recommended-asc", "newest": "days-on-redfin-asc",
    "price_low": "price-asc", "price_high": "price-desc",
    "sold_date": "last-sale-date-desc",
}
RENT_SORTS = {
    "recommended": "redfin-recommended-asc", "newest": "days-on-redfin-asc",
    "price_low": "price-asc", "price_high": "price-desc",
}
# the site's /filter/sort=... words -> ours
URL_SORT_WORDS = {
    "lo-days": "newest", "hi-days": "newest", "lo-price": "price_low", "hi-price": "price_high",
    "hi-sale-date": "sold_date", "redfin-recommended": "recommended",
}
# the site's /filter/property-type=... words -> ours
URL_PROPERTY_TYPE_WORDS = {
    "house": "house", "condo": "condo", "townhouse": "townhouse",
    "multifamily": "multi_family", "multi-family": "multi_family", "land": "land",
    "other": "other", "manufactured": "manufactured", "co-op": "co_op",
    "apartment": "apartment",
}
# searchStatus of a home record
SEARCH_STATUS_NAMES = {1: "active", 2: "pending", 4: "sold", 8: "coming_soon", 16: "off_market",
                       32: "off_market", 128: "pending"}
LISTING_TYPE_NAMES = {1: "mls", 2: "mls_foreclosure", 3: "for_sale_by_owner",
                      4: "foreclosure", 5: "new_construction", 6: "new_construction", 7: "mls"}

# rental buildings live at /<state>/<city>/<name>/apartment/<propertyId>
_HOME_LINK_RE = re.compile(r"/(?:home|apartment)/(\d+)")
_REGION_LINK_RE = re.compile(r"^/(neighborhood|zipcode|state|county|city|school|school-district)/([^/]+)(?:/|$)")
_REGION_ID_RE = re.compile(r"^(neighborhood|zipcode|county|city|school|school-district|state)/(\d{1,9})$", re.I)
_AGENT_LINK_RE = re.compile(r"/real-estate-agents/([a-z0-9][a-z0-9-]*)", re.I)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$", re.I)
_ZIP_RE = re.compile(r"^\d{5}$")


def _is_link(value):
    return value.startswith(("http://", "https://", "//", "www.redfin.com", "redfin.com"))


def _parse_link(value):
    """A redfin.com link -> its path (unquoted), or raise ValueError."""
    url = value
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if not (host == HOST or host.endswith("." + HOST)):
        raise ValueError("Must be a redfin.com link.")
    return unquote(parts.path or "/"), parts


# ---- locations -----------------------------------------------------------------

def region_id(region_type, table_id):
    """Our public region id: "<url word>/<table id>" ("city/30818")."""
    word = WORD_OF_REGION_TYPE.get(region_type)
    return f"{word}/{table_id}" if word and table_id is not None else None


def region_link(path):
    return SITE + path if path else None


def _split_filters(path):
    """'/city/30818/TX/Austin/filter/min-beds=3' -> (region path, filter string)."""
    marker = "/filter/"
    if marker in path:
        head, tail = path.split(marker, 1)
        return head, tail.split("/page-")[0]
    return path, None


def resolve_location(value):
    """Region id / link / free text -> one of
         {"kind": "region", "region_type": int, "region_id": int, "filters": str|None,
          "is_rentals": bool}
         {"kind": "query", "query": str}            # resolved by autocomplete
    A /zipcode/<zip> id or link and a /state/<name> link become queries
    (their ids are not in the URL). Raises ValueError with a user-facing
    message."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    if _is_link(value):
        path, _ = _parse_link(value)
        head, filters = _split_filters(path)
        is_rentals = "/apartments-for-rent" in head or "/houses-for-rent" in head
        head = head.replace("/apartments-for-rent", "").replace("/houses-for-rent", "")
        head = head.split("/housing-market")[0].split("/recently-sold")[0]
        head = head.split("/real-estate/agents")[0].split("/real-estate-agents")[0]
        match = _REGION_LINK_RE.match(head)
        if not match:
            raise ValueError("Must be a redfin.com region link (city, zipcode, neighborhood, "
                             "county, school, school-district or state page).")
        word, ident = match.group(1), match.group(2)
        if word == "zipcode":
            if not _ZIP_RE.match(ident):
                raise ValueError("A zipcode link must carry a 5-digit ZIP (/zipcode/78704).")
            return {"kind": "query", "query": ident, "filters": filters, "is_rentals": is_rentals}
        if word == "state":
            return {"kind": "query", "query": ident.replace("-", " "), "filters": filters,
                    "is_rentals": is_rentals}
        if not ident.isdigit():
            raise ValueError(f"A {word} link must carry a numeric id (/{word}/<id>/...).")
        return {"kind": "region", "region_type": REGION_TYPE_OF_WORD[word], "region_id": int(ident),
                "filters": filters, "is_rentals": is_rentals}
    match = _REGION_ID_RE.match(value)
    if match:
        word, ident = match.group(1).lower(), match.group(2)
        if word == "zipcode":
            if not _ZIP_RE.match(ident):
                raise ValueError("A zipcode id must be the 5-digit ZIP (zipcode/78704).")
            return {"kind": "query", "query": ident, "filters": None, "is_rentals": False}
        return {"kind": "region", "region_type": REGION_TYPE_OF_WORD[word], "region_id": int(ident),
                "filters": None, "is_rentals": False}
    if "/" in value and not " " in value:
        raise ValueError("Must be a region id like city/30818, a redfin.com region link, "
                         "or a place name / ZIP code.")
    return {"kind": "query", "query": value, "filters": None, "is_rentals": False}


# ---- properties ----------------------------------------------------------------

def resolve_property(value):
    """propertyId / home link / street address -> one of
         {"kind": "id", "property_id": int, "path": str|None}
         {"kind": "address", "address": str}      # resolved by autocomplete"""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    if value.isdigit():
        if len(value) > 12:
            raise ValueError("Must be a Redfin propertyId (digits), a redfin.com home link "
                             "or a street address.")
        return {"kind": "id", "property_id": int(value), "path": None}
    if _is_link(value):
        path, _ = _parse_link(value)
        match = _HOME_LINK_RE.search(path)
        if not match:
            raise ValueError("Must be a redfin.com home link "
                             "(https://www.redfin.com/TX/Austin/<street>/home/<propertyId>).")
        return {"kind": "id", "property_id": int(match.group(1)), "path": path}
    if not re.search(r"\d", value) or len(value) < 6:
        raise ValueError("Must be a Redfin propertyId, a redfin.com home link or a full "
                         "street address (e.g. 6005 Shoalwood Ave, Austin, TX 78757).")
    return {"kind": "address", "address": value}


def home_path_from_record(record):
    return (record or {}).get("url")


def home_link(path):
    return SITE + path if path else None


# ---- agents --------------------------------------------------------------------

def resolve_agent(value):
    """Agent slug or profile / reviews link -> the slug."""
    value = (value or "").strip()
    if not value:
        raise ValueError("Must not be empty.")
    if _is_link(value):
        path, _ = _parse_link(value)
        match = _AGENT_LINK_RE.search(path)
        if not match or match.group(1).lower() in ("team", "search"):
            raise ValueError("Must be a redfin.com agent profile link "
                             "(https://www.redfin.com/real-estate-agents/<slug>).")
        return match.group(1).lower()
    if "/" in value:
        match = _AGENT_LINK_RE.search(value)
        if match:
            return match.group(1).lower()
        raise ValueError("Must be an agent slug (david-tom) or a redfin.com agent profile link.")
    if not _SLUG_RE.match(value) or " " in value:
        raise ValueError("Must be an agent slug (david-tom) or a redfin.com agent profile link.")
    return value.lower()


def agent_link(slug):
    return f"{SITE}/real-estate-agents/{slug}" if slug else None


# ---- the site's /filter/ string ---------------------------------------------------

_UNIT_RE = re.compile(r"^([0-9]*\.?[0-9]+)\s*(k|m)?(?:-(sqft|acre|acres))?$", re.I)


def filter_number(text):
    """'400k' -> 400000, '1.5k-sqft' -> 1500, '1-acre' -> 43560, '2020' -> 2020."""
    match = _UNIT_RE.match((text or "").strip().lower())
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2) == "k":
        number *= 1000
    elif match.group(2) == "m":
        number *= 1_000_000
    if match.group(3) in ("acre", "acres"):
        number *= 43560
    return int(round(number))


def parse_filter_string(filters):
    """The site's '/filter/min-price=400k,max-price=800k,min-beds=3,...' ->
    our own search params (the keys of schemas.SearchSaleSchema /
    SearchRentSchema). Unknown filters are ignored. Returns (params, sold)
    where `sold` is the sold_within days when the filter had include=sold-*."""
    params, sold = {}, None
    for item in (filters or "").split(","):
        if "=" not in item:
            key, value = item.strip(), "true"
        else:
            key, value = item.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key in ("min-price", "max-price", "min-sqft", "max-sqft", "min-lot-size", "max-lot-size",
                   "min-year-built", "max-year-built", "min-beds", "max-beds", "min-baths",
                   "hoa", "min-stories", "max-stories"):
            number = filter_number(value)
            if key == "min-baths":
                # half baths are real ('min-baths=1.5'): keep the fraction
                try:
                    number = float(value)
                except ValueError:
                    number = None
                else:
                    number = int(number) if number.is_integer() else number
            if number is None:
                continue
            params[key.replace("-", "_").replace("min_lot_size", "min_lot_sqft")
                   .replace("max_lot_size", "max_lot_sqft").replace("hoa", "max_hoa")] = number
        elif key == "property-type":
            types = [URL_PROPERTY_TYPE_WORDS.get(w.strip().lower()) for w in value.split("+")]
            types = [t for t in types if t]
            if types:
                params["property_types"] = ",".join(types)
        elif key == "sort":
            if value.lower() in URL_SORT_WORDS:
                params["sort"] = URL_SORT_WORDS[value.lower()]
        elif key == "include" and value.lower().startswith("sold-"):
            unit = value.lower()[len("sold-"):]
            table = {"1wk": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1yr": 365, "2yr": 730,
                     "3yr": 1095, "5yr": 1825, "all": 1825}
            sold = table.get(unit, 90)
        elif key == "status":
            words = {w.strip().lower() for w in value.split("+")}
            if words == {"active"}:
                params["status"] = "active"
            elif words == {"comingsoon"}:
                params["status"] = "coming_soon"
            elif "pending" in words or "contingent" in words:
                params["status"] = "active_and_pending" if "active" in words else "pending"
    return params, sold
