"""Redfin stingray JSON -> clean records. Pure functions, no I/O.

Fixtures (redfin/fixtures/, captured 2026-10-31 over a
datacenter exit with a Mac-minted aws-waf-token for the walled calls):
  property_<tag>_<section>.json   austin (active, Texas non-disclosure market),
                                  seattle (coming soon, NWMLS), sold (Closed,
                                  Seattle), offmarket (Palo Alto public record,
                                  no listingId)
  gis_sale_austin / gis_sold_seattle / gis_empty.json      search pages
  rentals_search_austin.json, rental_*.json                 rentals
  autocomplete_*.json, region_breadcrumbs_*.json            locations
  agent_*.json, agents_directory_city.json                  agents
  market_*.json                                             housing market
  notfound_*.json, market_insights_page_bad.json            error envelopes

Conventions: snake_case, `link` for URLs, booleans as questions, ISO dates
(the site's epoch milliseconds are local-midnight stamps, so the UTC date is
the site's date), numbers as numbers, null for anything the site hides
(displayLevel 5 / a missing value), presentation strings dropped.
"""
import re
from datetime import datetime, timezone

from redfin.refs import (
    AUTOCOMPLETE_KIND, HOME_PROPERTY_TYPE_NAMES, LISTING_TYPE_NAMES, PROPERTY_TYPE_NAMES,
    REGION_TYPE_OF_AUTOCOMPLETE, SEARCH_STATUS_NAMES, SITE, WORD_OF_REGION_TYPE,
    agent_link, home_link, region_id,
)

PHOTO_CDN = "https://ssl.cdn-redfin.com/photo"
_REGION_PATH_RE = re.compile(r"^/(neighborhood|zipcode|state|county|city|school|school-district)/([^/]+)")
_TAG_RE = re.compile(r"<[^>]+>")
_MONEY_RE = re.compile(r"^([+-])?\$?([0-9][0-9,]*\.?[0-9]*)\s*([KMB])?(%|\s*pt)?$", re.I)


# ---- value helpers ------------------------------------------------------------------

def _get(data, *keys, default=None):
    """Nested .get() that tolerates None / non-dicts along the way."""
    cur = data
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int) and -len(cur) <= key < len(cur):
            cur = cur[key]
        else:
            return default
        if cur is None:
            return default
    return cur


def _lv(value):
    """A {"value": x, "level": n} cell -> x (None when the site hides it)."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def _number(value):
    """'$758K' -> 758000, '95.7%' -> 95.7, '+1.9%' -> 1.9, '1,404' -> 1404,
    '-6.1 pt' -> -6.1; a number passes through; anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    match = _MONEY_RE.match(value.strip())
    if not match:
        return None
    sign, digits, unit, _suffix = match.groups()
    try:
        number = float(digits.replace(",", ""))
    except ValueError:
        return None
    number *= {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get((unit or "").lower(), 1)
    if sign == "-":
        number = -number
    return int(number) if number == int(number) else round(number, 4)


def _int(value):
    number = _number(value)
    return int(number) if isinstance(number, (int, float)) else None


def _float(value):
    number = _number(value)
    return float(number) if isinstance(number, (int, float)) else None


def _date_ms(ms):
    """Epoch milliseconds -> 'YYYY-MM-DD' (UTC; the site stamps local midnight)."""
    try:
        if ms in (None, "", 0):
            return None
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _datetime_ms(ms):
    try:
        if ms in (None, "", 0):
            return None
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso(value):
    """'2026-10-28T20:32:58.346339Z' -> '2026-10-28T20:32:58Z'; dates pass through."""
    if not isinstance(value, str) or not value:
        return None
    if len(value) >= 19 and value[10] == "T":
        return value[:19] + "Z"
    return value


def _text(value):
    """Strip HTML tags and collapse whitespace; '' -> None."""
    if value is None:
        return None
    text = " ".join(_TAG_RE.sub(" ", str(value)).split())
    return text or None


def _str(value):
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _bool(value):
    return bool(value) if value is not None else None


def _link(path):
    if not path:
        return None
    return path if path.startswith("http") else SITE + path


def _days(ms):
    """A timeOnRedfin duration in milliseconds -> days (one decimal)."""
    number = _number(ms)
    if number is None:
        return None
    return round(number / 86_400_000, 1)


def _region_id_of_path(path):
    match = _REGION_PATH_RE.match(path or "")
    if not match:
        return None
    word, ident = match.group(1), match.group(2)
    return f"{word}/{ident}"


def _street_address(info):
    """The site's split address -> one street line + unit."""
    if not isinstance(info, dict):
        return None, None
    street = info.get("assembledAddress")
    if not street:
        parts = [info.get("streetNumber"), info.get("directionalPrefix"), info.get("streetName"),
                 info.get("streetType"), info.get("directionalSuffix")]
        street = " ".join(p for p in parts if p) or None
    unit = " ".join(p for p in (info.get("unitType"), info.get("unitValue")) if p) or None
    if street and unit and unit in street:
        street = street.replace(unit, "").strip()
    return street, unit


# ---- photos --------------------------------------------------------------------------

def _photo_links(data_source_id, mls_id, ranges):
    """The gis card's compressed photo list ('0-39:0,40:1') -> CDN links of
    the full-size photos, the way the site builds them
    (photo/<dataSourceId>/bigphoto/<last 3 digits of mlsId>/<mlsId>_<i>[_<version>].jpg)."""
    mls_id = _str(mls_id)
    if not ranges or not mls_id or data_source_id is None:
        return []
    links = []
    subdir = mls_id[-3:]
    for part in str(ranges).split(","):
        span, _, version = part.partition(":")
        lo, _, hi = span.partition("-")
        try:
            lo_i = int(lo)
            hi_i = int(hi) if hi else lo_i
        except ValueError:
            continue
        for i in range(lo_i, min(hi_i, lo_i + 200) + 1):
            # the site's names: <mls>_0.jpg for the first photo at version 0,
            # <mls>_<i>_<version>.jpg for every other (fixtures, 2026-10-31)
            name = f"{mls_id}_0" if i == 0 and version in ("", "0") else f"{mls_id}_{i}_{version or '0'}"
            links.append(f"{PHOTO_CDN}/{data_source_id}/bigphoto/{subdir}/{name}.jpg")
    return links


# ---- locations -----------------------------------------------------------------------

def parse_autocomplete(payload):
    """location-autocomplete payload -> rows. Regions carry our region id
    ("city/30818"), addresses the propertyId, agent directories the region
    they cover; the site's "no match" row (type 23) is dropped."""
    rows = []
    for section in _get(payload, "sections", default=[]) or []:
        for row in section.get("rows") or []:
            kind = AUTOCOMPLETE_KIND.get(str(row.get("type")))
            if not kind:
                continue
            url = row.get("url") or row.get("urlV2")
            ident = None
            if kind == "address":
                tail = str(row.get("id") or "").split("_", 1)[-1]
                ident = int(tail) if tail.isdigit() else None
            elif kind == "zipcode":
                ident = f"zipcode/{row.get('name')}" if row.get("name") else None
            elif kind == "building":
                ident = _region_id_of_path(url) or (f"building/{str(row.get('id')).split('_')[-1]}")
            else:
                ident = _region_id_of_path(url)
            region_type = REGION_TYPE_OF_AUTOCOMPLETE.get(str(row.get("type")))
            rows.append({
                "id": ident,
                "kind": kind,
                "name": _str(row.get("name")),
                "region": _str(row.get("subName")),
                "link": _link(url),
                "region_type": WORD_OF_REGION_TYPE.get(region_type) if region_type else None,
                "is_active": _bool(row.get("active")),
                "has_rental": _bool(row.get("hasRental")) if "hasRental" in row else None,
                "country": _str(row.get("countryCode")),
            })
    return rows


def parse_breadcrumbs(payload):
    """region-breadcrumbs payload -> {name, link, trail: [{name, link, type}]}."""
    crumbs = []
    for crumb in _get(payload, "breadcrumbs", default=[]) or []:
        crumbs.append({"name": _str(crumb.get("name")), "link": _link(crumb.get("url")),
                       "type": _str(crumb.get("breadcrumbType"))})
    last = crumbs[-1] if crumbs else {}
    return {"name": last.get("name"), "link": last.get("link"), "trail": crumbs}


# ---- search cards --------------------------------------------------------------------

def _address_of_card(home):
    lat_lng = _lv(home.get("latLong")) or {}
    return {
        "street": _str(_lv(home.get("streetLine"))),
        "unit": _str(_lv(home.get("unitNumber"))),
        "city": _str(home.get("city")),
        "state": _str(home.get("state")),
        "zip": _str(home.get("zip") or _lv(home.get("postalCode"))),
        "country": _str(home.get("countryCode")),
        "neighborhood": _str(_lv(home.get("location"))),
        "latitude": lat_lng.get("latitude") if isinstance(lat_lng, dict) else None,
        "longitude": lat_lng.get("longitude") if isinstance(lat_lng, dict) else None,
    }


def _agent_of_card(home):
    agent = home.get("listingAgent") or {}
    broker = home.get("listingBroker") or {}
    if not agent and not broker:
        return None
    return {"name": _str(agent.get("name")), "redfin_agent_id": agent.get("redfinAgentId"),
            "broker": _str(broker.get("name")), "is_redfin": _bool(broker.get("isRedfin"))}


def parse_home(home):
    """One gis home card (search, sold, similar, recently sold, nearby open
    houses) -> a flat listing record."""
    if not isinstance(home, dict):
        return None
    price = home.get("price")
    mls_id = _lv(home.get("mlsId"))
    photos = _photo_links(home.get("dataSourceId"), mls_id, _lv(home.get("photos")))
    open_start, open_end = home.get("openHouseStart"), home.get("openHouseEnd")
    selling = home.get("sellingBroker") or {}
    return {
        "property_id": home.get("propertyId"),
        "listing_id": home.get("listingId"),
        "mls_id": _str(mls_id),
        "link": home_link(home.get("url")),
        "status": _str(home.get("mlsStatus")),
        "search_status": SEARCH_STATUS_NAMES.get(home.get("searchStatus")),
        "listing_type": LISTING_TYPE_NAMES.get(home.get("listingType")),
        "property_type": PROPERTY_TYPE_NAMES.get(home.get("uiPropertyType"))
        or HOME_PROPERTY_TYPE_NAMES.get(home.get("propertyType")),
        "price": _number(_lv(price)),
        "is_price_hidden": _bool(home.get("hideSalePrice")),
        "price_per_sqft": _number(_lv(home.get("pricePerSqFt"))),
        "hoa_monthly": _number(_lv(home.get("hoa"))),
        "beds": home.get("beds"),
        "baths": home.get("baths"),
        "full_baths": home.get("fullBaths"),
        "partial_baths": home.get("partialBaths"),
        "sqft": _number(_lv(home.get("sqFt"))),
        "lot_sqft": _number(_lv(home.get("lotSize"))),
        "stories": home.get("stories"),
        "year_built": _number(_lv(home.get("yearBuilt"))),
        "address": _address_of_card(home),
        "days_on_market": _number(_lv(home.get("dom"))),
        "time_on_redfin_days": _days(_lv(home.get("timeOnRedfin"))),
        "sold_date": _date_ms(home.get("soldDate")),
        "listing_agent": _agent_of_card(home),
        "selling_broker": _str(selling.get("name")) if isinstance(selling, dict) else None,
        "remarks": _text(home.get("listingRemarks")),
        "key_facts": [f.get("description") for f in home.get("keyFacts") or [] if f.get("description")],
        "tags": [t for t in home.get("listingTags") or [] if t],
        "badges": [s.get("sashTypeName") for s in home.get("sashes") or [] if s.get("sashTypeName")],
        "open_house": {"start": _datetime_ms(open_start), "end": _datetime_ms(open_end),
                       "name": _str(home.get("openHouseEventName"))} if open_start else None,
        "photos": {"count": home.get("numPictures") if home.get("numPictures") is not None else (len(photos) or None),
                   "primary": photos[0] if photos else None, "links": photos},
        "is_hot": _bool(home.get("isHot")),
        "is_new_construction": _bool(home.get("isNewConstruction")),
        "is_redfin_listing": _bool(home.get("isRedfin")),
        "is_early_access": _bool(home.get("isEarlyAccess")),
        "has_virtual_tour": _bool(home.get("hasVirtualTour")),
        "has_video_tour": _bool(home.get("hasVideoTour")),
        "has_3d_tour": _bool(home.get("has3DTour")),
        "has_interactive_floorplan": _bool(home.get("hasInteractiveFloorplan")),
        "garage_spaces": home.get("skGarageSpaces"),
        "parking_spaces": home.get("skParkingSpaces"),
        "timezone": _str(home.get("timeZone")),
    }


def parse_search(payload):
    """gis payload -> (homes, median) where median is the page's searchMedian."""
    homes = [parse_home(h) for h in _get(payload, "homes", default=[]) or []]
    median = payload.get("searchMedian") if isinstance(payload, dict) else None
    median_out = None
    if isinstance(median, dict):
        median_out = {"price": _number(median.get("price")), "sqft": _number(median.get("sqFt")),
                      "price_per_sqft": _number(median.get("pricePerSqFt")),
                      "days_on_market": _number(median.get("dom")), "beds": median.get("beds"),
                      "baths": median.get("baths")}
    return [h for h in homes if h], median_out


def parse_rental_home(home):
    """One v1/search/rentals (or homecards) entry -> a rental building record."""
    if not isinstance(home, dict):
        return None
    data = home.get("homeData") or {}
    ext = home.get("rentalExtension") or {}
    addr = data.get("addressInfo") or {}
    centroid = _get(addr, "centroid", "centroid", default={}) or {}
    ranges = ext.get("rentPriceRange") or {}
    beds, baths, sqft = ext.get("bedRange") or {}, ext.get("bathRange") or {}, ext.get("sqftRange") or {}
    photo_count = sum(max(0, (r.get("endPos") or 0) - (r.get("startPos") or 0) + 1)
                      for r in _get(data, "photosInfo", "photoRanges", default=[]) or [])
    return {
        "property_id": _int(data.get("propertyId")),
        "rental_id": _str(ext.get("rentalId")),
        "name": _str(ext.get("propertyName")),
        "link": home_link(data.get("url")),
        "property_type": HOME_PROPERTY_TYPE_NAMES.get(data.get("propertyType")),
        "rent": {"min": _number(ranges.get("min")), "max": _number(ranges.get("max")), "currency": "USD"},
        "is_total_price": _bool(ext.get("isTotalPrice")),
        "beds": {"min": beds.get("min"), "max": beds.get("max")},
        "baths": {"min": baths.get("min"), "max": baths.get("max")},
        "sqft": {"min": sqft.get("min"), "max": sqft.get("max")},
        "available_units": ext.get("numAvailableUnits"),
        "address": {
            "street": _str(addr.get("formattedStreetLine")),
            "unit": _str(addr.get("unitNumber")),
            "city": _str(addr.get("city")),
            "state": _str(addr.get("state")),
            "zip": _str(addr.get("zip")),
            "neighborhood": _str(addr.get("location")),
            "latitude": centroid.get("latitude"),
            "longitude": centroid.get("longitude"),
        },
        "phone": _str(ext.get("desktopPhone") or ext.get("mobileWebPhone")),
        "description": _text(ext.get("description")),
        "key_facts": [f.get("description") for f in ext.get("keyFacts") or [] if f.get("description")],
        "badges": [s.get("sashTypeName") for s in data.get("sashes") or [] if s.get("sashTypeName")],
        "photo_count": photo_count or None,
        "feed_source": _str(ext.get("feedSource") or ext.get("feedOriginalSource")),
        "external_link": _str(ext.get("rentalPropertyExternalUrl")),
        "listed_at": _iso(ext.get("freshnessTimestamp")),
        "last_updated": _iso(ext.get("lastUpdated")),
        "is_income_restricted": _bool(ext.get("isIncomeRestricted")),
        "is_senior_living": _bool(ext.get("isSeniorLiving")),
        "is_student_housing": _bool(ext.get("isStudent")),
        "is_military_housing": _bool(ext.get("isMilitary")),
        "has_fiber": _bool(data.get("hasAttFiber")),
    }


def parse_rentals_search(data):
    """v1/search/rentals JSON -> (homes, matched_homes, matched_units)."""
    homes = [parse_rental_home(h) for h in (data or {}).get("homes") or []]
    return [h for h in homes if h], (data or {}).get("numMatchedHomes"), (data or {}).get("numMatchedUnits")


# ---- property details ----------------------------------------------------------------

def _agent_entry(entry):
    info = entry.get("agentInfo") or {}
    return {
        "name": _str(info.get("agentName")),
        "broker": _str(entry.get("brokerName")),
        "license": _str(entry.get("license")),
        "is_redfin_agent": _bool(info.get("isRedfinAgent")),
        "is_partner_agent": _bool(info.get("isPartnerAgent")),
    }


def _humanize_reference(reference):
    """An amenity's internal reference name, used when the site shows no
    label: 'POOL_DESCRIPTION' -> 'Pool Description', 'GARAGE_YN' -> 'Garage'."""
    words = (_str(reference) or "").replace("-", " ").replace("_", " ").split()
    if words and words[-1].upper() == "YN":
        words = words[:-1]
    return " ".join(w.capitalize() for w in words) or None


def _amenities(info):
    out = []
    for group in _get(info, "superGroups", default=[]) or []:
        sections = []
        for section in group.get("amenityGroups") or []:
            facts = []
            for entry in section.get("amenityEntries") or []:
                values = [v for v in entry.get("amenityValues") or [] if v not in (None, "", "—")]
                name = _str(entry.get("amenityName")) or _humanize_reference(entry.get("referenceName"))
                if name and values:
                    facts.append({"name": name, "values": values})
            if facts:
                sections.append({"title": _str(section.get("groupTitle")), "facts": facts})
        if sections:
            out.append({"category": _str(group.get("titleString")), "sections": sections})
    return out


def _tax_row(row):
    return {"roll_year": row.get("rollYear"), "taxes_due": _number(row.get("taxesDue")),
            "land_value": _number(row.get("taxableLandValue")),
            "improvement_value": _number(row.get("taxableImprovementValue"))}


def _public_records(info):
    if not isinstance(info, dict):
        return None
    basic = info.get("basicInfo") or {}
    latest = info.get("latestListingInfo") or {}
    tax = info.get("taxInfo")
    return {
        "beds": basic.get("beds"),
        "baths": basic.get("baths"),
        "property_type": _str(basic.get("propertyTypeName")),
        "stories": basic.get("numStories"),
        "year_built": basic.get("yearBuilt"),
        "finished_sqft": basic.get("sqFtFinished"),
        "total_sqft": basic.get("totalSqFt"),
        "lot_sqft": basic.get("lotSqFt"),
        "apn": _str(basic.get("apn")),
        "county": _str(info.get("countyName")),
        "county_link": _link(info.get("countyUrl")),
        "last_updated": _date_ms(basic.get("propertyLastUpdatedDate")),
        "tax": _tax_row(tax) if isinstance(tax, dict) else None,
        "tax_history": [_tax_row(r) for r in info.get("allTaxInfo") or [] if isinstance(r, dict)],
        "latest_listing": {
            "beds": latest.get("beds"), "baths": latest.get("baths"), "total_sqft": latest.get("totalSqFt"),
            "lot_sqft": latest.get("lotSqFt"), "stories": latest.get("numStories"),
            "property_type": _str(latest.get("propertyTypeName")), "year_built": latest.get("yearBuilt"),
        } if latest else None,
    }


def _mortgage(info):
    if not isinstance(info, dict):
        return None
    rates = info.get("mortgageRateInfo") or {}
    insurance = info.get("homeInsuranceData") or {}
    return {
        "listing_price": _number(info.get("listingPrice")),
        "down_payment_percent": info.get("downPaymentPercentage"),
        "property_tax_rate_percent": info.get("propertyTaxRate"),
        "home_insurance_rate_percent": info.get("homeInsuranceRate"),
        "home_insurance_yearly_estimate": _number(insurance.get("homeInsuranceYearlyEstimate")),
        "rates": {
            "thirty_year_fixed": rates.get("thirtyYearFixed"),
            "twenty_year_fixed": rates.get("twentyYearFixed"),
            "fifteen_year_fixed": rates.get("fifteenYearFixed"),
            "fha_thirty_year_fixed": rates.get("fhaThirtyYearFixed"),
            "va_thirty_year_fixed": rates.get("vaThirtyYearFixed"),
            "thirty_year_fixed_jumbo": rates.get("thirtyYearFixedJumbo"),
        } if rates else None,
        "county": _str(info.get("countyName")),
        "state": _str(info.get("stateName")),
    }


def _history_events(info):
    events = []
    for event in _get(info, "events", default=[]) or []:
        if not isinstance(event, dict):
            continue
        hidden = event.get("priceDisplayLevel") == 5 or bool(event.get("isPriceAdminOnly"))
        events.append({
            "date": _date_ms(event.get("eventDate")),
            "event": _str(event.get("eventDescription")),
            "mls_status": _str(event.get("mlsDescription")),
            "price": None if hidden else _number(event.get("price")),
            "is_price_hidden": hidden,
            "appreciation": event.get("appreciation"),
            "source": _str(event.get("source")),
            "source_listing_id": _str(event.get("sourceId")),
            "note": _text(event.get("eventSubDescription")),
        })
    return events


def _photos_of_media(media):
    if not isinstance(media, dict):
        return None
    links, thumbnails = [], []
    for photo in media.get("photos") or []:
        urls = photo.get("photoUrls") or {}
        link = urls.get("fullScreenPhotoUrl") or urls.get("nonFullScreenPhotoUrl")
        if link:
            links.append(link)
        thumb = _get(photo, "thumbnailData", "thumbnailUrl")
        if thumb:
            thumbnails.append(thumb)
    floorplan = media.get("interactiveFloorplan") or {}
    street = media.get("streetView") or {}
    return {
        "count": len(links) or None,
        "primary": links[0] if links else None,
        "links": links,
        "thumbnails": thumbnails,
        "videos": [v.get("videoUrl") or v.get("url") for v in media.get("videos") or [] if isinstance(v, dict)],
        "scans": [s.get("scanUrl") or s.get("url") for s in media.get("scans") or [] if isinstance(s, dict)],
        "interactive_floorplan_link": _str(floorplan.get("scanUrl")),
        "street_view_link": _str(street.get("streetViewUrl")) if street.get("streetViewAvailable") else None,
        "previous_listing_photos_count": media.get("previousListingPhotosCount"),
    }


def _market_competition(info):
    compete = _get(info, "competeScoreInfo")
    if not isinstance(compete, dict):
        return None
    aggs = compete.get("aggs") or {}
    return {
        "region": _str(compete.get("regionName")),
        "region_link": _link(compete.get("regionUrl")),
        "compete_score": aggs.get("score"),
        "sale_to_list_ratio": aggs.get("saleToList"),
        "hot_homes_sale_to_list_ratio": aggs.get("hhSaleToList"),
        "days_on_market": aggs.get("dom"),
        "hot_homes_days_on_market": aggs.get("hhDom"),
        "average_offers": aggs.get("numOffers"),
        "waived_contingency_share": aggs.get("waivedContingency"),
        "lookback_months": aggs.get("lookBack"),
        "potential_buyers_180_days": _get(compete, "additionalRegionAggs", "potential_buyer_count_180"),
        "nearby": [{"region": _str(n.get("regionName")), "region_link": _link(n.get("regionUrl")),
                    "compete_score": _get(n, "scoreAggs", "score"),
                    "sale_to_list_ratio": _get(n, "scoreAggs", "saleToList"),
                    "days_on_market": _get(n, "scoreAggs", "dom")}
                   for n in compete.get("nearbyCompeteScores") or []],
    }


def _location_scores(score):
    if not isinstance(score, dict):
        return None
    keys = {
        "pedestrian_friendly": "pedestrianFriendlyScore", "cycling_friendly": "cyclingFriendlyScore",
        "transit_friendly": "transitFriendlyScore", "car_friendly": "carFriendlyScore",
        "parks": "parksScore", "groceries": "groceriesScore", "shopping": "shoppingScore",
        "nightlife": "nightlifeScore", "restaurants": "restaurantsScore", "cafes": "cafesScore",
        "daycares": "daycaresScore", "primary_schools": "primarySchoolsScore",
        "high_schools": "highSchoolsScore", "quiet": "quietScore", "greenery": "greeneryScore",
        "vibrant": "vibrantScore", "wellness": "wellnessScore",
    }
    out = {name: (round(score[key], 2) if isinstance(score.get(key), (int, float)) else None)
           for name, key in keys.items()}
    out["last_updated"] = _iso(score.get("lastUpdatedDate"))
    return out


def _walk_scores(stats):
    data = _get(stats, "walkScoreInfo", "walkScoreData")
    if not isinstance(data, dict):
        return None
    walk = data.get("walkScore") or {}
    return {
        "walk_score": _number(walk.get("value")),
        "walk_score_label": _str(walk.get("shortDescription")),
        "bike_score": _number(_get(data, "bikeScore", "value")),
        "transit_score": _number(_get(data, "transitScore", "value")),
        "link": _str(walk.get("link")),
    }


def _zoning_rows(zoning):
    out = []
    for row in zoning or []:
        if not isinstance(row, dict):
            continue
        out.append({
            "code": _str(row.get("zoningCode")),
            "type": _str(row.get("zoningType")),
            "category": _str(row.get("zoningCategory")),
            "subcategory": _str(row.get("zoningSubcategory")),
            "description": _str(row.get("zoningDescription")),
            "summary": _text(row.get("zoningSummary")),
            "jurisdiction": _str(row.get("jurisdictionFullName")),
            "jurisdiction_type": _str(row.get("jurisdictionType")),
            "parcel_share_percent": row.get("intersectPct"),
            "ordinance_link": _str(row.get("ordinanceDocumentUrl")),
            "ordinance_date": _iso(row.get("ordinanceVintage"))[:10] if _iso(row.get("ordinanceVintage")) else None,
        })
    return out


def parse_property(sections):
    """The merged property record from the per-section payloads:
    initial (initialInfo), above (aboveTheFold), main (mainHouseInfoPanelInfo),
    below (v1 belowTheFold), avm, parcel, rent_estimate, market,
    location_score, stats (neighborhoodStats), status_banner, neighborhood,
    popularity, zoning, hot_market, sun. Every section may be None."""
    initial = sections.get("initial") or {}
    above = sections.get("above") or {}
    main = _get(sections.get("main"), "mainHouseInfo", default={}) or {}
    open_houses = _get(sections.get("main"), "openHouseInfo", "openHouseList", default=[]) or []
    below = sections.get("below") or {}
    avm = sections.get("avm") or {}
    parcel = sections.get("parcel") or {}
    rent = _get(sections.get("rent_estimate"), "rentalEstimateInfo", default={}) or {}
    addr = above.get("addressSectionInfo") or {}
    media = above.get("mediaBrowserInfo") or {}
    extra = above.get("additionalBrokerageInfo") or {}
    street, unit = _street_address(addr.get("streetAddress"))
    lat_lng = addr.get("latLong") or {}
    status = (main.get("mlsStatusDisplay") or addr.get("status") or {})
    price_info = addr.get("priceInfo") or {}
    latest_price = addr.get("latestPriceInfo") or {}
    remarks = [r.get("marketingRemark") for r in main.get("marketingRemarks") or [] if r.get("marketingRemark")]
    public = _public_records(below.get("publicRecordsInfo"))
    county = (public or {}).get("county")
    search_status = main.get("searchStatus") or addr.get("searchStatus") or initial.get("searchStatus")
    property_id = initial.get("propertyId") or _get(addr, "avmInfo", "propertyId") or avm.get("propertyId")
    listing_id = initial.get("listingId") or main.get("listingId")
    path = main.get("url") or addr.get("url")
    listed_price = _number(price_info.get("amount"))
    predicted = avm.get("predictedValue")
    main_open = main.get("openHouses") or []
    return {
        "property_id": property_id,
        "listing_id": listing_id,
        "mls_id": _str(main.get("mlsId")) or _str(initial.get("mlsId")),
        "link": home_link(path),
        "status": _str(status.get("displayValue")) or _str(addr.get("homeStatusLabel")),
        "status_definition": _str(status.get("definition")),
        "search_status": SEARCH_STATUS_NAMES.get(search_status),
        "is_off_market": search_status in (16, 32) if search_status is not None else None,
        "listing_source": _str(_get(main, "source", "dataSourceName")) or _str(addr.get("source")),
        "property_type": HOME_PROPERTY_TYPE_NAMES.get(addr.get("propertyType")),
        "listing_type": LISTING_TYPE_NAMES.get(addr.get("listingType")),
        "price": listed_price,
        "price_label": _str(price_info.get("label")),
        "latest_price": _number(latest_price.get("amount")),
        "price_per_sqft": _number(addr.get("pricePerSqFt")),
        "beds": addr.get("beds"),
        "baths": addr.get("baths"),
        "full_baths": addr.get("numFullBaths"),
        "sqft": _number(_lv(addr.get("sqFt"))),
        "lot_sqft": _number(addr.get("lotSize")),
        "year_built": addr.get("yearBuilt"),
        "stories": (public or {}).get("stories"),
        "address": {
            "street": street,
            "unit": unit,
            "city": _str(addr.get("city")),
            "state": _str(addr.get("state")),
            "zip": _str(addr.get("zip")),
            "country": _str(addr.get("countryCode")),
            "county": county,
            "neighborhood": _str(_get(sections.get("neighborhood"), "neighborhoodName")),
            "full": _str(main.get("fullStreetAddress")),
            "latitude": lat_lng.get("latitude"),
            "longitude": lat_lng.get("longitude"),
        },
        "description": _text(" ".join(remarks)) if remarks else None,
        "highlights": [{"name": _str(a.get("header")), "value": _str(a.get("content"))}
                       for a in main.get("selectedAmenities") or [] if a.get("header")],
        "listing_agents": [_agent_entry(a) for a in main.get("listingAgents") or []],
        "buying_agents": [_agent_entry(a) for a in main.get("buyingAgents") or []],
        "listing_contact_phone": _str(main.get("listingContactInfo")),
        "co_agent": {"name": _str(extra.get("coAgentName")), "email": _str(extra.get("coAgentEmail")),
                     "phone": _str(extra.get("coAgentPhone")),
                     "brokerage_link": _str(extra.get("brokerageWebsite"))} if extra.get("coAgentName") else None,
        "open_houses": [{"start": _datetime_ms(o.get("startTime")), "end": _datetime_ms(o.get("endTime")),
                         "comment": _str(o.get("comment"))} for o in (main_open or open_houses)],
        "photos": _photos_of_media(media),
        "badges": [s.get("sashTypeName") for s in media.get("sashes") or [] if s.get("sashTypeName")],
        "amenities": _amenities(below.get("amenitiesInfo")),
        "amenities_count": _get(below, "amenitiesInfo", "totalAmenities"),
        "price_history": _history_events(below.get("propertyHistoryInfo")),
        "public_records": public,
        "mortgage": _mortgage(_get(below, "publicRecordsInfo", "mortgageCalculatorInfo")),
        "redfin_estimate": {
            "value": _number(predicted),
            "is_available": predicted is not None,
            "last_sold_price": _number(avm.get("lastSoldPrice")),
            "last_sold_date": _date_ms(avm.get("lastSoldDate") or avm.get("soldDate")),
            "comparables_count": len(avm.get("comparables") or []) or None,
        } if avm else None,
        "rent_estimate": {
            "value": _number(rent.get("predictedValue")),
            "low": _number(rent.get("predictedValueLow")),
            "high": _number(rent.get("predictedValueHigh")),
            "currency": "USD",
        } if rent.get("predictedValue") is not None else None,
        "market_competition": _market_competition(sections.get("market")),
        "walk_scores": _walk_scores(sections.get("stats")),
        "location_scores": _location_scores(sections.get("location_score")),
        "sun_exposure": {
            "hours_june": _get(sections.get("sun"), "sunHoursJune"),
            "hours_december": _get(sections.get("sun"), "sunHoursDecember"),
            "hours_annual": _get(sections.get("sun"), "sunHoursAnnual"),
            "percentile": _get(sections.get("sun"), "relativePercentile"),
        } if sections.get("sun") else None,
        "parcel": {
            "apn": _str(parcel.get("apn") or addr.get("apn")),
            "fips": _str(parcel.get("fipsCode") or addr.get("fips")),
            "polygon": _str(parcel.get("polygon")),
        } if (parcel or addr.get("apn")) else None,
        "zoning": _zoning_rows(sections.get("zoning")) or None,
        "popularity": {
            "views": _get(sections.get("popularity"), "numHomeViews"),
            "is_popular": _bool(_get(sections.get("popularity"), "isPopular")),
        } if sections.get("popularity") else None,
        "hot_market": {
            "share_off_market_fast_percent": _get(sections.get("hot_market"), "percentageOfHomesOffMarket"),
            "weeks": _get(sections.get("hot_market"), "numberOfWeeks"),
        } if sections.get("hot_market") else None,
        "status_banner": _str(_get(sections.get("status_banner"), "segments", 0, "text")),
        "is_new_construction": _bool(main.get("isNewConstruction")),
        "is_coming_soon": _bool(main.get("isComingSoonListing")),
        "has_offer_deadline": _bool(main.get("hasOfferDeadlineInEffect")),
        "is_likely_under_contract": _bool(main.get("isLikelyUnderContractOrRecentlySold")),
        "is_hot": _bool(addr.get("isHot")),
        "is_redfin_listing": _bool(addr.get("isRedfin")),
        "time_on_redfin_days": _days(addr.get("timeOnRedfin")),
        "timezone": _str(main.get("timezone") or addr.get("soldDateTimeZone")),
        "last_updated": _datetime_ms(main.get("lastUpdatedDate")),
        "last_checked": _datetime_ms(main.get("lastCheckedDate")),
    }


def _series(rows, updated_ms=None):
    """avmHistoricalData series: bare monthly values, oldest first, the last
    one being the month of `avmUpdateDate` (60 points = 5 years) -> rows
    with a 'YYYY-MM' month."""
    values = [v for v in rows or [] if isinstance(v, (int, float)) or isinstance(v, dict)]
    if not values:
        return []
    end = datetime.fromtimestamp(int(updated_ms) / 1000, tz=timezone.utc) if updated_ms else datetime.now(timezone.utc)
    out = []
    count = len(values)
    for index, value in enumerate(values):
        months_back = count - 1 - index
        year, month = end.year, end.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        if isinstance(value, dict):
            value = value.get("value", value.get("avm"))
        out.append({"month": f"{year:04d}-{month:02d}", "value": _number(value)})
    return out


def _comparable(home):
    street, unit = _street_address(home.get("streetAddress"))
    return {
        "property_id": home.get("propertyId"),
        "listing_id": home.get("listingId"),
        "link": home_link(home.get("url")),
        "status": _str(_get(home, "status", "displayValue")),
        "price": None if home.get("hideSalePrice") else _number(_get(home, "priceInfo", "amount")),
        "price_label": _str(_get(home, "priceInfo", "label")),
        "is_price_hidden": _bool(home.get("hideSalePrice")),
        "sold_date": _date_ms(home.get("soldDate")),
        "beds": home.get("beds"),
        "baths": home.get("baths"),
        "sqft": _number(_lv(home.get("sqFt"))),
        "address": {"street": street, "unit": unit, "city": _str(home.get("city")),
                    "state": _str(home.get("state")), "zip": _str(home.get("zip")),
                    "latitude": _get(home, "latLong", "latitude"), "longitude": _get(home, "latLong", "longitude")},
        "mls_id": _str(home.get("mlsId")),
        "listing_broker": _str(home.get("broker")),
        "selling_broker": _str(home.get("sellingBroker")),
        "badges": [s.get("sashTypeName") for s in home.get("sashes") or [] if s.get("sashTypeName")],
    }


def parse_estimate(avm, history, similars):
    """avm + avmHistoricalData + similars/listings -> the valuation record."""
    avm = avm or {}
    history = history or {}
    predicted = avm.get("predictedValue")
    sales = []
    for row in history.get("saleHistory") or []:
        if isinstance(row, dict):
            sales.append({"date": _date_ms(row.get("date") or row.get("saleDate") or row.get("soldDate")),
                          "price": _number(row.get("price") or row.get("salePrice") or row.get("value"))})
    return {
        "property_id": avm.get("propertyId"),
        "redfin_estimate": _number(predicted),
        "is_available": predicted is not None,
        "estimate_updated": _datetime_ms(history.get("avmUpdateDate")),
        "listing_price": _number(avm.get("listingPrice")),
        "last_sold_price": _number(avm.get("lastSoldPrice")),
        "last_sold_date": _date_ms(avm.get("lastSoldDate") or avm.get("soldDate")),
        "beds": avm.get("numBeds"),
        "baths": avm.get("numBaths"),
        "sqft": _number(_lv(avm.get("sqFt"))),
        "year_built": history.get("yearBuilt"),
        "is_visibility_restricted": _bool(avm.get("isVisibilityRestricted")),
        "comparables": [_comparable(h) for h in avm.get("comparables") or [] if isinstance(h, dict)],
        "sale_history": sales,
        "value_history": {
            "property": _series(history.get("propertyTimeSeries"), history.get("avmUpdateDate")),
            "zip": _series(history.get("postalCodeTimeSeries"), history.get("avmUpdateDate")),
            "city": _series(history.get("cityTimeSeries"), history.get("avmUpdateDate")),
            "county": _series(history.get("countyTimeSeries"), history.get("avmUpdateDate")),
        } if history else None,
        "similar_listings": {
            "min_price": _number(_get(similars, "minPrice")),
            "max_price": _number(_get(similars, "maxPrice")),
            "average_price_per_sqft": _number(_get(similars, "aveDolSF")),
            "homes": [parse_home(h) for h in _get(similars, "homes", default=[]) or []],
        } if similars else None,
    }


# ---- schools / climate / permits ---------------------------------------------------

def _school(row):
    district = row.get("schoolDistrict") or {}
    levels = [name for name, key in (("elementary", "elementary"), ("middle", "middle"), ("high", "high"))
              if row.get(key)]
    return {
        "id": row.get("id"),
        "name": _str(row.get("name")),
        "link": _link(row.get("schoolUrl") or row.get("searchUrl")),
        "type": _str(row.get("institutionType")),
        "grades": _str(row.get("gradeRanges")),
        "levels": levels,
        "rating": row.get("greatSchoolsRating"),
        "parent_rating": row.get("parentRating"),
        "serves_home": _bool(row.get("servesHome")),
        "distance_miles": _float(row.get("distanceInMiles")),
        "students": row.get("numberOfStudents"),
        "student_teacher_ratio": row.get("studentToTeacherRatio"),
        "address": _str(row.get("fullAddress")),
        "website": _str(row.get("websiteUrl")),
        "greatschools_link": _str(row.get("greatSchoolOverviewUrl")),
        "review_count": row.get("numReviews"),
        "reviews": [{"date": _str(r.get("datePosted")), "by": _str(r.get("reviewedBy")),
                     "rating": r.get("reviewerRating"), "text": _text(r.get("review"))}
                    for r in row.get("schoolReviews") or []],
        "sub_ratings": [{"type": _str(r.get("schoolGranularRatingType")),
                         "value": _number(r.get("schoolGranularRatingValue")),
                         "summary": _str(r.get("schoolGranularRatingCopyText"))}
                        for r in row.get("schoolGranularRatings") or []],
        "district": {"id": district.get("id"), "name": _str(district.get("districtName")),
                     "address": _str(district.get("address")), "city": _str(district.get("city")),
                     "state": _str(district.get("stateCode")), "zip": _str(district.get("zip")),
                     "website": _str(district.get("websiteUrl"))} if district else None,
        "rating_updated": _str(row.get("lastUpdatedDate")),
    }


def parse_schools(payload):
    """schoolsAndDistrictsInfo payload -> {serving_home, nearby, districts}."""
    payload = payload or {}
    serving = [_school(r) for r in payload.get("servingThisHomeSchools") or [] if isinstance(r, dict)]
    serving_ids = {s["id"] for s in serving}
    nearby = [_school(r) for r in payload.get("schoolsToShowOnDP") or []
              if isinstance(r, dict) and r.get("id") not in serving_ids]
    districts = [{"id": d.get("districtId"), "name": _str(d.get("districtName")),
                  "website": _str(d.get("districtUrl")), "has_choice_schools": _bool(d.get("hasChoiceSchools"))}
                 for d in payload.get("districtsServingThisHome") or [] if isinstance(d, dict)]
    return {"schools_serving_home": serving, "nearby_schools": nearby, "districts": districts,
            "summary": _str(payload.get("sectionPreviewText"))}


def _risk_direction(value):
    """The site's riskDirection is a word (fire / heat) or a signed number (flood)."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, (int, float)):
        return "increasing" if value > 0 else ("decreasing" if value < 0 else "stationary")
    return None


def _risk(block, factor_key):
    if not isinstance(block, dict) or not block:
        return None
    summary = block.get("expandableSummary")
    return {
        "factor": block.get(factor_key) if factor_key in block else block.get("riskFactorScore"),
        "label": _str(block.get("expandableHeading")),
        "summary": _str(summary.get("value") if isinstance(summary, dict) else summary),
        "risk_direction": _risk_direction(block.get("riskDirection")),
        "insurance_low": _number(block.get("lowInsurancePrice")),
        "insurance_high": _number(block.get("highInsurancePrice")),
    }


def parse_climate_risk(payload):
    """riskFactorData payload -> flood / fire / heat / wind / air factors."""
    payload = payload or {}
    flood = payload.get("floodData") or {}
    heat = payload.get("heatData") or {}
    fire = payload.get("fireData") or {}
    wind_v2 = payload.get("windData") or {}
    air = payload.get("airData") or {}
    out = {
        "report_link": _str(payload.get("riskFactorPropertyUrl")),
        "flood": _risk(flood, "floodFactor"),
        "fire": _risk(fire, "fireFactor"),
        "heat": _risk(heat, "heatFactor"),
        "wind": _risk(wind_v2, "riskFactorScore"),
        "air": _risk(air, "riskFactorScore"),
    }
    if out["flood"] is not None:
        out["flood"]["fema_zones"] = [z for z in flood.get("femaZones") or [] if z]
        out["flood"]["cumulative_chance"] = [{"year": c.get("year"), "low": c.get("low"), "mid": c.get("mid"),
                                              "high": c.get("high")} for c in flood.get("cumulative") or []]
    if out["fire"] is not None:
        out["fire"]["relative_risk"] = fire.get("relativeRisk")
        out["fire"]["cumulative_risk_30_years_percent"] = fire.get("cumulativeRiskYear30")
    if out["heat"] is not None:
        out["heat"]["hot_days_this_year"] = heat.get("cumulativeRiskYear0")
        out["heat"]["hot_days_in_30_years"] = heat.get("cumulativeRiskYear30")
        out["heat"]["cooling_cost_increase_30_years_percent"] = heat.get("coolingCostPercentIncrease30")
    if out["air"] is not None:
        out["air"]["unhealthy_days_this_year"] = air.get("graphRiskYear0")
        out["air"]["unhealthy_days_in_30_years"] = air.get("graphRiskYear30")
    return out


def parse_permits(permits, zoning):
    """v2 permits list + parcel-zoning list -> {permits, zoning}."""
    rows = []
    for permit in permits or []:
        if not isinstance(permit, dict):
            continue
        rows.append({
            "number": _str(permit.get("permitNumber")),
            "status": _str(permit.get("status")),
            "type": _str(permit.get("type")),
            "subtype": _str(permit.get("subtype")),
            "description": _text(permit.get("description")),
            "summary": _text(permit.get("descriptionDerived")),
            "tags": [t for t in permit.get("permitTagArray") or [] if t],
            "file_date": (_iso(permit.get("fileDate")) or "")[:10] or None,
            "issue_date": (_iso(permit.get("issueDate")) or "")[:10] or None,
            "start_date": (_iso(permit.get("startDate")) or "")[:10] or None,
            "end_date": (_iso(permit.get("endDate")) or "")[:10] or None,
            "final_date": (_iso(permit.get("finalDate")) or "")[:10] or None,
        })
    rows.sort(key=lambda r: r.get("file_date") or "", reverse=True)
    return {"permits": rows, "permits_count": len(rows), "zoning": _zoning_rows(zoning)}


# ---- rentals -------------------------------------------------------------------------

def _unit(unit):
    return {
        "name": _str(unit.get("name")),
        "beds": unit.get("bedrooms"),
        "full_baths": unit.get("fullBaths"),
        "half_baths": unit.get("halfBaths"),
        "sqft": unit.get("sqft"),
        "rent": _number(unit.get("rentPrice")),
        "deposit": _number(unit.get("deposit")),
        "currency": _str(unit.get("rentCurrency")) or "USD",
        "status": _str(unit.get("status")),
        "available_date": _str(unit.get("availableDate")),
        "amenities": [a for a in unit.get("specificAmenities") or [] if a],
    }


def _floor_plans(payload):
    plans = []
    seen = set()
    for group in _get(payload, "unitTypesByBedroom", default=[]) or []:
        for plan in group.get("availableUnitTypes") or []:
            ident = plan.get("unitTypeId")
            if ident in seen:
                continue
            seen.add(ident)
            plans.append({
                "id": _str(ident),
                "name": _str(plan.get("name")),
                "beds": plan.get("bedrooms"),
                "full_baths": plan.get("fullBaths"),
                "half_baths": plan.get("halfBaths"),
                "sqft": {"min": plan.get("sqftMin"), "max": plan.get("sqftMax")},
                "rent": {"min": _number(plan.get("rentPriceMin")), "max": _number(plan.get("rentPriceMax")),
                         "currency": _str(plan.get("rentCurrency")) or "USD"},
                "deposit": _number(plan.get("deposit")),
                "status": _str(plan.get("status")),
                "available_units": plan.get("availableUnits"),
                "total_units": plan.get("totalUnits"),
                "lease_terms": [t for t in plan.get("availableLeaseTerms") or [] if t],
                "amenities": [a for a in plan.get("specificAmenities") or [] if a],
                "units": [_unit(u) for u in plan.get("units") or [] if isinstance(u, dict)],
            })
    return plans


def parse_rental(card, about, floor_plans, fees, amenities, contacts, media, engagement, schools):
    """The rental building record from homecards + the v1/rentals/<id> sections."""
    base = parse_rental_home(card) if card else {}
    about = about or {}
    fees = fees or {}
    amenities = amenities or {}
    contacts = contacts or {}
    engagement = engagement or {}
    plans = _floor_plans(floor_plans)
    units = [u for p in plans for u in p["units"]]
    rents = [u["rent"] for u in units if u.get("rent") is not None]
    schedule = contacts.get("leasingOfficeSchedules") or about.get("leasingOfficeSchedules") or []
    record = dict(base)
    record.update({
        "name": base.get("name") or _str(contacts.get("propertyName")),
        "description": _text(about.get("description")) or base.get("description"),
        "management_company": _str(_get(about, "managementCompany", "name")),
        "phone": _str(contacts.get("desktopPhone") or contacts.get("mobileWebPhone")) or base.get("phone"),
        "has_online_application": _bool(contacts.get("hasApplication")),
        "leasing_office_hours": [{"day": _str(s.get("day")), "open": _str(s.get("open")),
                                  "close": _str(s.get("close"))} for s in schedule],
        "floor_plans": plans,
        "floor_plans_count": _get(floor_plans, "totalUnitTypes") or (len(plans) or None),
        "total_units": _get(floor_plans, "totalUnits"),
        "has_price_drop": _bool(_get(floor_plans, "priceDrop")),
        "unit_rent": {"min": min(rents) if rents else (base.get("rent") or {}).get("min"),
                      "max": max(rents) if rents else (base.get("rent") or {}).get("max"), "currency": "USD"},
        "fees": {
            "application": _number(fees.get("applicationFee")),
            "admin": _number(fees.get("adminFee")),
            "deposit_min": _number(fees.get("depositFeeMin")),
            "deposit_max": _number(fees.get("depositFeeMax")),
            "lease_terms": [t for t in fees.get("availableLeaseTerms") or [] if t],
        } if fees else None,
        "parking": [{"type": _str(p.get("parkingTypeName")), "fee": _number(p.get("parkingFee")),
                     "is_assigned": _bool(p.get("isAssigned")), "note": _text(p.get("parkingComment"))}
                    for p in fees.get("parkingTypes") or []],
        "pet_policies": [{"pet": _str(p.get("policyName")), "fee": _number(p.get("petFee")),
                          "deposit": _number(p.get("petDeposit")), "monthly_rent": _number(p.get("petRent")),
                          "allowed_count": p.get("allowedPetCount"), "note": _text(p.get("petPolicyComment"))}
                         for p in fees.get("petPolicies") or []],
        "amenities": {
            "unit": [a for a in amenities.get("unitAmenities") or [] if a],
            "community": [a for a in amenities.get("communityAmenities") or [] if a],
            "standardized": [a for a in amenities.get("standardizedAmenities") or [] if a],
            "other": [a for a in amenities.get("otherAmenities") or [] if a],
        } if amenities else None,
        "engagement": {
            "views": engagement.get("viewCount"),
            "favorites": engagement.get("favoriteCount"),
            "is_top_10_percent": _bool(engagement.get("isTop10Percent")),
        } if engagement else None,
        "schools": parse_schools(schools) if schools else None,
        "has_street_view": _bool(_get(media, "streetView", "streetViewAvailable")),
    })
    return record


# ---- agents --------------------------------------------------------------------------

def _photo_link(path):
    if not path:
        return None
    if path.startswith("http"):
        return path
    return "https://ssl.cdn-redfin.com/system_files" + path if path.startswith("/images/") else _link(path)


def parse_agent_directory(data):
    """radp filterable-agents-data/V3 JSON -> agent cards."""
    rows = []
    for agent in _get(data, "payload", "agents", default=[]) or []:
        if not isinstance(agent, dict):
            continue
        rows.append({
            "id": agent.get("agentId"),
            "slug": _str(agent.get("slug")),
            "name": _str(agent.get("fullName") or agent.get("name")),
            "link": _link(agent.get("profileUrl")) or agent_link(agent.get("slug")),
            "title": _str(agent.get("jobTitle")),
            "brokerage": _str(agent.get("brokerageName")),
            "email": _str(agent.get("email")),
            "phone": _str(agent.get("phoneNumber")),
            "photo": _str(agent.get("photoUrl640x460") or agent.get("photoUrl")) if agent.get("hasPhotos") else None,
            "languages": [l for l in agent.get("languages") or [] if l],
            "service_regions": [r for r in agent.get("primaryServiceRegions") or [] if r],
            "business_market": _str(agent.get("businessMarket")),
            "office_state": _str(agent.get("officeState")),
            "deals_past_year": agent.get("pastYearDeals"),
            "deals_past_year_in_region": agent.get("pastYearDealsInRegion"),
            "deals_lifetime": agent.get("homeTransactionsLifetime"),
            "volume_lifetime": _number(agent.get("transactionVolumeLifetime")),
            "is_partner_agent": _bool(agent.get("partner")),
            "is_premier": _bool(agent.get("isPremier")),
            "is_veteran": _bool(agent.get("veteran")),
            "is_active": _bool(agent.get("isActive")),
            "serves_buyers": _bool(agent.get("servesOffers")),
            "serves_sellers": _bool(agent.get("servesListings")),
        })
    return rows


def _review(row):
    review = row.get("review") or {}
    deal = row.get("deal") or {}
    prop = row.get("property") or {}
    badges = row.get("badges") or {}
    return {
        "id": review.get("surveyResponseId"),
        "rating": review.get("rating"),
        "stars": review.get("starRating"),
        "date": _str(review.get("reviewDate")),
        "year": _int(review.get("reviewYear")),
        "text": _text(review.get("fullReviewText") or review.get("reviewText")),
        "link": _link(review.get("reviewUrl")),
        "reviewer": " ".join(p for p in (review.get("customerFirstName"), review.get("customerLastName")) if p) or None,
        "deal": {
            "side": "buyer" if deal.get("isBuyer") else ("seller" if deal.get("isBuyer") is False else None),
            "was_successful": _bool(deal.get("dealSuccessful")),
            "price": _number(deal.get("displayDealPrice")),
            "property_type": _str(deal.get("displayPropertyType")),
            "closed_date": _date_ms(deal.get("closeDate")),
            "is_first_time_buyer": _bool(badges.get("isFirstTimeBuyer")),
            "had_multiple_offers": _bool(badges.get("isMultipleOffers")),
            "is_new_construction": _bool(badges.get("isNewConstruction")),
        } if deal else None,
        "property": {
            "address": _str(prop.get("displayAddress")),
            "city": _str(prop.get("cityAddress")),
            "state": _str(prop.get("stateCodeAddress")),
            "zip": _str(prop.get("zipcodeAddress")),
            "neighborhood": _str(prop.get("neighborhoodName")),
            "link": _link(prop.get("propertyUrl")),
            "beds": prop.get("numBeds"),
            "baths": prop.get("numBaths"),
            "sqft": prop.get("sqFt"),
            "photo": _str(review.get("homePhotoUrl")),
        } if prop else None,
    }


def parse_agent(profile, rollup, stats, phone, counts, photo, listings_sell, listings_buy, endorsements):
    """The agent profile record from the agent-profile/* and agents/data/* calls."""
    profile = profile or {}
    rollup = rollup or {}
    stats = stats or {}
    counts = counts or {}
    photo = photo or {}
    city = rollup.get("city") or {}
    sell_homes = _get(listings_sell, "gisHomes", "homes", default=[]) or []
    buy_homes = _get(listings_buy, "gisHomes", "homes", default=[]) or []
    slug = _str(profile.get("agentSlug"))
    photo_path = photo.get("8") or photo.get("9") or photo.get("1")
    return {
        "id": profile.get("agentId"),
        "slug": slug,
        "name": " ".join(p for p in (profile.get("firstName"), profile.get("lastName")) if p) or None,
        "link": _link(profile.get("profileUrl")) or agent_link(slug),
        "title": _str(profile.get("jobTitle")),
        "license_number": _str(profile.get("licenseNumber")),
        "license_class": _str(profile.get("licenseClassTitle")),
        "email": _str(profile.get("agentEmail")),
        "phone": _str(_get(phone, "phoneNumber")),
        "is_taking_calls": _bool(_get(phone, "currentlyTakingCalls")),
        "photo": _photo_link(photo_path),
        "bio": _text(profile.get("personalInfo")),
        "languages": [l for l in profile.get("languages") or profile.get("unfilteredLanguages") or [] if l],
        "started_year": profile.get("startYear"),
        "team_link": _link(profile.get("teamUrl")),
        "team": [{"id": m.get("agentId"), "name": " ".join(p for p in (m.get("firstName"), m.get("lastName")) if p) or None,
                  "role": _str(m.get("jobName")), "photo": _photo_link(m.get("agentPhotoUrl"))}
                 for m in rollup.get("teamMembers") or []],
        "city": _str(city.get("name")),
        "state": _str(rollup.get("stateCode")),
        "service_regions": [{"id": region_id(r.get("regionType"), _get(r, "regionId", "tableId") or r.get("id")),
                             "name": _str(r.get("name")), "link": _link(r.get("mapLink"))}
                            for r in rollup.get("selectedRegions") or []],
        "rating": profile.get("avgRating"),
        "rating_out_of_5": profile.get("avgRatingForCustomerDisplay"),
        "review_count": counts.get("numReviews"),
        "review_breakdown": {"five_star": counts.get("num5starReviews"), "four_star": counts.get("num4starReviews"),
                             "three_star": counts.get("num3starReviews"), "two_star": counts.get("num2starReviews"),
                             "one_star": counts.get("num1starReviews")} if counts else None,
        "reviews_link": _link(profile.get("allReviewsUrl")),
        "deals_closed": profile.get("numHomesClosed") or rollup.get("numDeals"),
        "average_response_minutes": rollup.get("avgResponseMinutes"),
        "stats": {
            "transactions_last_year": stats.get("homeTransactionsLastYear"),
            "volume_last_year": _number(stats.get("transactionVolumeLastYear")),
            "average_price_last_year": _number(stats.get("averageTransactionPriceLastYear")),
            "highest_price_last_year": _number(stats.get("highestTransactionPriceLastYear")),
            "transactions_lifetime": stats.get("homeTransactionsLifetime"),
            "volume_lifetime": _number(stats.get("transactionVolumeLifetime")),
            "highest_price_lifetime": _number(stats.get("highestTransactionPriceLifetime")),
            "homes_sold_last_year": stats.get("homesSoldLastYear"),
            "sales_volume_last_year": _number(stats.get("salesVolumeLastYear")),
            "homes_purchased_last_year": stats.get("homesPurchasedLastYear"),
            "purchase_volume_last_year": _number(stats.get("purchaseVolumeLastYear")),
        } if stats else None,
        "active_listings": [parse_home(h) for h in sell_homes if isinstance(h, dict)],
        "active_buyer_homes": [parse_home(h) for h in buy_homes if isinstance(h, dict)],
        "market": _str(_get(listings_sell, "agentMarketInfo", "displayName")),
        "serves_buyers": _bool(_get(listings_sell, "servesOffers")),
        "serves_sellers": _bool(_get(listings_sell, "servesListings")),
        "is_premier": _bool(profile.get("isPremier")),
        "is_presidents_club": _bool(_get(endorsements, "isPresidentsClub")),
        "accolades": [a for a in _get(endorsements, "accolades", default=[]) or [] if a],
        "accepts_contact": _bool(profile.get("allowContactThroughProfile")),
    }


def parse_agent_reviews(payload):
    """all-reviews payload ({year: [rows]}) -> reviews newest year first."""
    rows = []
    years = sorted((y for y in (payload or {}) if str(y).isdigit()), key=int, reverse=True)
    for year in years:
        for row in payload.get(year) or []:
            if isinstance(row, dict):
                rows.append(_review(row))
    return rows


# ---- housing market ------------------------------------------------------------------

def _graph_metrics(payload):
    out = {}
    for metric in _get(payload, "metrics", default=[]) or []:
        label = _str(metric.get("label"))
        if not label:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_").replace("of_", "")
        out[key] = {
            "latest": _number(metric.get("value")),
            "series": [{"date": _str(r.get("date")), "value": _number(r.get("value")),
                        "year_over_year": _str(r.get("yoy"))} for r in metric.get("aggregateData") or []],
        }
    return out


def parse_market_overview(insights, trends, compete, prices, demand, affordability, climate, walk, nearby):
    """market-insights-page + trends + compete-score + the two graphs +
    affordability + climate-data + walk-score-data + nearby-compete-scores."""
    region = _get(insights, "region", default={}) or {}
    page = _get(insights, "page", default={}) or {}
    trends = trends or {}
    score = _get(compete, "scoreAggs", 0, default={}) or {}
    affordability = affordability or {}
    climate = climate or {}
    walk = walk or {}
    region_type = region.get("type")
    return {
        "region": {
            "id": region_id(region_type, region.get("id")),
            "name": _str(region.get("name")),
            "type": WORD_OF_REGION_TYPE.get(region_type),
            "state": _str(region.get("stateCode")),
            "link": _link(page.get("canonicalUrl")),
            "housing_market_link": _link(page.get("marketInsightsPageUrl")),
        },
        "title": _str(page.get("h1")),
        "summary": _str(page.get("metaDescription")),
        "trends": {
            "median_sale_price": _number(trends.get("medianSalePrice")),
            "median_list_price": _number(trends.get("medianListPrice")),
            "median_sale_price_per_sqft": _number(trends.get("medianSalePerSqFt")),
            "median_list_price_per_sqft": _number(trends.get("medianListPerSqFt")),
            "median_sale_to_list_percent": _number(trends.get("medianSalePerList")),
            "homes_sold": _number(trends.get("numHomesSold")),
            "homes_on_market": _number(trends.get("numHomesOnMarket")),
            "average_days_on_market": _number(trends.get("avgDaysOnMarket")),
            "average_offers": _number(trends.get("avgNumOffers")),
            "average_down_payment_percent": _number(trends.get("avgDownPayment")),
            "sale_price_year_over_year_percent": _number(trends.get("yoySalePrice")),
            "sale_price_per_sqft_year_over_year_percent": _number(trends.get("yoySalePerSqft")),
        } if trends else None,
        "compete_score": {
            "score": score.get("score"),
            "sale_to_list_ratio": score.get("saleToList"),
            "hot_homes_sale_to_list_ratio": score.get("hhSaleToList"),
            "days_on_market": score.get("dom"),
            "hot_homes_days_on_market": score.get("hhDom"),
            "average_offers": score.get("numOffers"),
            "waived_contingency_share": score.get("waivedContingency"),
            "lookback_months": score.get("lookBack"),
        } if score else None,
        "nearby_compete_scores": [{"region": _str(n.get("regionName")), "link": _link(n.get("regionUrl")),
                                   "score": _get(n, "scoreAggs", "score"),
                                   "sale_to_list_ratio": _get(n, "scoreAggs", "saleToList"),
                                   "days_on_market": _get(n, "scoreAggs", "dom")}
                                  for n in _get(nearby, "nearbyCompeteScores", default=[]) or []],
        "price_history": _graph_metrics(prices) or None,
        "demand_history": _graph_metrics(demand) or None,
        "affordability": {
            "as_of": _date_ms(affordability.get("latestAggregateDate")),
            "median_price_yearly_trend_percent": affordability.get("mediumPriceYearlyTrend"),
            "active_listings_yearly_trend_percent": affordability.get("activeListingYearlyTrend"),
            "by_bedrooms": [{"beds": a.get("aggregationType"),
                             "active_listings": _get(a, "aggregate", "activeListingsCount"),
                             "median_list_price": _number(_get(a, "aggregate", "listPriceMedian")),
                             "search_link": _link(a.get("searchUrl"))}
                            for a in affordability.get("bedroomAggregates") or []],
        } if affordability else None,
        "climate": {
            "county": _str(climate.get("county")),
            "flood": _region_risk(climate.get("flood")),
            "fire": _region_risk(climate.get("fire")),
            "heat": _region_risk(climate.get("heat")),
            "wind": _region_risk(climate.get("wind")),
        } if climate else None,
        "walk_scores": {
            "walk_score": _number(_get(walk, "walkScore", "value")),
            "bike_score": _number(_get(walk, "bikeScore", "value")),
            "transit_score": _number(_get(walk, "transitScore", "value")),
            "population": _number(walk.get("population")),
            "jobs": _number(walk.get("jobs")),
        } if walk else None,
    }


def _region_risk(block):
    if not isinstance(block, dict):
        return None
    return {
        "score": block.get("score"),
        "label": _str(block.get("scoreText")),
        "properties_at_risk": block.get("propertiesAtRisk"),
        "properties_at_risk_percent": (round(block["percentagePropertiesAtRisk"] * 100, 2)
                                       if isinstance(block.get("percentagePropertiesAtRisk"), (int, float)) else None),
        "total_properties": block.get("totalProperties"),
        "link": _str(block.get("link")),
    }


def parse_recently_sold(payload):
    """gis/recently-sold payload -> homes + the region's link."""
    homes = [parse_home(h) for h in _get(payload, "gisHomes", "homes", default=[]) or []]
    return {"homes": [h for h in homes if h], "link": _link(_get(payload, "url"))}


def parse_best_schools(payload):
    """best-schools payload -> schools grouped by level."""
    payload = payload or {}
    return {
        "elementary": [_school(r) for r in payload.get("elementarySchools") or [] if isinstance(r, dict)],
        "middle": [_school(r) for r in payload.get("middleSchools") or [] if isinstance(r, dict)],
        "high": [_school(r) for r in payload.get("highSchools") or [] if isinstance(r, dict)],
        "counts": {"elementary": payload.get("elementarySchoolCount"), "middle": payload.get("middleSchoolCount"),
                   "high": payload.get("highSchoolCount")},
    }
