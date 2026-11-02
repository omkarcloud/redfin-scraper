"""The /redfin/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs). Caching lives here (cache.cached_call),
keyed on the validated params:

  * the autocomplete rows and the region validation are cached for days and
    shared by every endpoint that takes a `location`;
  * a property's (propertyId, listingId, path) triple is cached once and
    shared by details / estimate / schools / climate-risk / permits, so a
    client hitting all five pays the token calls once;
  * the details record is ONE cached fan-out of the site's sections.
"""
import math

from cache import DontCache, cached_call
from redfin import cache_config as ttl
from redfin import parsers, refs
from redfin import fetch as F
from redfin.fetch import RedfinBadRequest, RedfinNotFound, get_payload, get_payload_optional, get_raw, run_parallel
from redfin.schemas import PAGE_SIZE, RENTAL_PAGE_SIZE

AGENTS_PAGE_SIZE = 50
SALE_FILTER_PARAMS = (
    ("min_price", "min_price"), ("max_price", "max_price"), ("min_beds", "num_beds"),
    ("max_beds", "max_num_beds"), ("min_baths", "num_baths"), ("min_sqft", "min_listing_approx_size"),
    ("max_sqft", "max_listing_approx_size"), ("min_lot_sqft", "min_parcel_size"),
    ("max_lot_sqft", "max_parcel_size"), ("min_year_built", "min_year_built"),
    ("max_year_built", "max_year_built"), ("max_hoa", "hoa"), ("min_stories", "min_stories"),
    ("max_stories", "max_stories"),
)
_REGION_KINDS = ("city", "zipcode", "neighborhood", "county", "school", "school_district", "state")


# ---- locations -------------------------------------------------------------------------

def _autocomplete(query):
    payload = get_payload("/stingray/do/location-autocomplete",
                          {"location": query, "v": 2, "al": 1}, label="location-autocomplete", token=True)
    return parsers.parse_autocomplete(payload), payload


def _autocomplete_cached(query):
    """(rows, raw payload) for a query, cached for days."""
    return cached_call("redfin.autocomplete", {"query": " ".join(query.lower().split())},
                       lambda query: list(_autocomplete(query)), ttl.LOCATIONS_CACHE)


def locations_search(query):
    rows, _ = _autocomplete_cached(query)
    return {"query": query, "count": len(rows), "results": rows}


# ---- regions ---------------------------------------------------------------------------

def _region_record(region_type, region_id):
    """Validate a region id against the site (region-breadcrumbs answers an
    error envelope for an unknown one) and name it."""
    payload = get_payload(f"/stingray/api/region/{region_type}/{region_id}/1/region-breadcrumbs",
                          label="region")
    crumbs = parsers.parse_breadcrumbs(payload)
    return {"id": refs.region_id(region_type, region_id), "type": refs.WORD_OF_REGION_TYPE.get(region_type),
            "name": crumbs.get("name"), "link": crumbs.get("link"), "region_type": region_type,
            "region_id": region_id}


def _region_record_cached(region_type, region_id):
    try:
        return cached_call("redfin.region", {"region_type": region_type, "region_id": region_id},
                           _region_record, ttl.REGION_CACHE)
    except RedfinNotFound:
        raise RedfinNotFound(f"Unknown Redfin region {refs.region_id(region_type, region_id)}")


def _region_from_query(query):
    """Free text / ZIP -> the first region row of the site's autocomplete."""
    rows, raw = _autocomplete_cached(query)
    want_zip = query.strip().isdigit() and len(query.strip()) == 5
    for section in (raw or {}).get("sections") or []:
        for row in section.get("rows") or []:
            kind = refs.AUTOCOMPLETE_KIND.get(str(row.get("type")))
            region_type = refs.REGION_TYPE_OF_AUTOCOMPLETE.get(str(row.get("type")))
            if not region_type or kind not in _REGION_KINDS:
                continue
            if want_zip and kind != "zipcode":
                continue
            ident = str(row.get("id") or "").split("_", 1)[-1]
            if not ident.isdigit():
                continue
            return {"id": refs.region_id(region_type, int(ident)) if kind != "zipcode"
                    else f"zipcode/{row.get('name')}",
                    "type": refs.WORD_OF_REGION_TYPE.get(region_type), "name": row.get("name"),
                    "link": refs.region_link(row.get("url")), "region_type": region_type,
                    "region_id": int(ident), "matched_query": query}
    raise RedfinNotFound(f"No Redfin region matches {query!r}")


def _region_from_query_cached(query):
    return cached_call("redfin.region_query", {"query": " ".join(query.lower().split())},
                       _region_from_query, ttl.REGION_CACHE)


def _region(location):
    """The resolved location dict (refs.resolve_location) -> region record."""
    if location["kind"] == "region":
        return _region_record_cached(location["region_type"], location["region_id"])
    return _region_from_query_cached(location["query"])


def _public_region(region):
    return {k: region.get(k) for k in ("id", "type", "name", "link")}


# ---- for-sale / sold search ----------------------------------------------------------------

def _gis_params(region, data, *, status, sort, sold_within=None, page=1, poly=None):
    types = data.get("property_types") or list(refs.PROPERTY_TYPES)
    # mpt=99 is the site's search-results page type: without it status=4
    # (sold) answers active homes (probe 2026-11-01)
    params = {
        "al": 1, "v": 8, "mpt": 99, "num_homes": PAGE_SIZE, "start": (page - 1) * PAGE_SIZE,
        "ord": refs.SALE_SORTS[sort], "sf": "1,2,3,5,6,7",
        "uipt": ",".join(str(refs.PROPERTY_TYPES[t]) for t in types), "status": status,
    }
    if sold_within:
        params["sold_within_days"] = sold_within
    if poly:
        params["poly"] = poly
    else:
        params["region_id"] = region["region_id"]
        params["region_type"] = region["region_type"]
    for ours, theirs in SALE_FILTER_PARAMS:
        value = data.get(ours)
        if value is not None:
            params[theirs] = int(value) if float(value).is_integer() else value
    return params


def _no_state(region):
    if region and region.get("region_type") == 4:
        raise ValueError("A state is too large for a listing search (the site's search service times "
                         "out); use a city, county, ZIP code, neighborhood or school region.")


def _sale_page(region, data, *, status, sort, sold_within=None, poly=None, label="for-sale search"):
    page = data.get("page") or 1
    payload = get_payload("/stingray/api/gis", _gis_params(region, data, status=status, sort=sort,
                                                           sold_within=sold_within, page=page, poly=poly),
                          label=label)
    homes, median = parsers.parse_search(payload)
    return {
        "location": _public_region(region) if region else None,
        "median": median,
        "results": homes,
        "pagination": {"page": page, "items_per_page": PAGE_SIZE, "has_next": len(homes) >= PAGE_SIZE,
                       "total_pages": None, "total_count": None},
    }


def _search_sale(**data):
    region = _region(data.pop("location"))
    _no_state(region)
    status = refs.SALE_STATUS[data.pop("status")]
    sort = data.pop("sort")
    return _sale_page(region, data, status=status, sort=sort)


def properties_search_sale(**data):
    return cached_call("redfin.search_sale", data, _search_sale, ttl.SEARCH_CACHE)


def _search_sold(**data):
    region = _region(data.pop("location"))
    _no_state(region)
    sold_within = data.pop("sold_within")
    sort = data.pop("sort")
    return _sale_page(region, data, status=refs.SOLD_STATUS, sort=sort, sold_within=sold_within,
                      label="sold search")


def properties_search_sold(**data):
    return cached_call("redfin.search_sold", data, _search_sold, ttl.SEARCH_CACHE)


def _search_coordinates(**data):
    poly = data.pop("area")
    listing = data.pop("listing")
    sort = data.pop("sort")
    if listing == "sold":
        return _sale_page(None, data, status=refs.SOLD_STATUS, sort=sort, sold_within=90, poly=poly,
                          label="sold search")
    if sort == "sold_date":
        sort = "recommended"
    return _sale_page(None, data, status=refs.SALE_STATUS["active_and_coming_soon"], sort=sort, poly=poly)


def properties_search_coordinates(**data):
    return cached_call("redfin.search_coordinates", data, _search_coordinates, ttl.SEARCH_CACHE)


# ---- rentals search --------------------------------------------------------------------

def _rent_params(region, data, page):
    types = data.get("property_types") or list(refs.RENTAL_PROPERTY_TYPES)
    params = {
        "al": 1, "isRentals": "true", "v": 8, "num_homes": RENTAL_PAGE_SIZE, "start": (page - 1) * RENTAL_PAGE_SIZE,
        "ord": refs.RENT_SORTS[data.get("sort") or "recommended"],
        "uipt": ",".join(str(refs.RENTAL_PROPERTY_TYPES[t]) for t in types),
        "region_id": region["region_id"], "region_type": region["region_type"],
    }
    for ours, theirs in (("min_rent", "min_price"), ("max_rent", "max_price"), ("min_beds", "num_beds"),
                         ("max_beds", "max_num_beds"), ("min_baths", "num_baths")):
        value = data.get(ours)
        if value is not None:
            params[theirs] = int(value) if float(value).is_integer() else value
    return params


def _search_rent(**data):
    region = _region(data.pop("location"))
    _no_state(region)
    page = data.get("page") or 1
    raw = get_raw("/stingray/api/v1/search/rentals", _rent_params(region, data, page), label="rentals search")
    homes, matched, units = parsers.parse_rentals_search(raw if isinstance(raw, dict) else {})
    total_pages = math.ceil(matched / RENTAL_PAGE_SIZE) if isinstance(matched, int) else None
    return {
        "location": _public_region(region),
        "matched_units": units,
        "results": homes,
        "pagination": {"page": page, "items_per_page": RENTAL_PAGE_SIZE, "total_pages": total_pages,
                       "total_count": matched},
    }


def properties_search_rent(**data):
    return cached_call("redfin.search_rent", data, _search_rent, ttl.SEARCH_CACHE)


# ---- search by site URL ---------------------------------------------------------------

def _search_url(url, page=1):
    location = refs.resolve_location(url)
    filters, sold_within = refs.parse_filter_string(location.get("filters"))
    if "property_types" in filters:
        filters["property_types"] = [t for t in filters["property_types"].split(",") if t]
    region = _region(location)
    _no_state(region)
    if location.get("is_rentals"):
        data = {"page": page, "sort": filters.get("sort") if filters.get("sort") in refs.RENT_SORTS else "recommended",
                "min_rent": filters.get("min_price"), "max_rent": filters.get("max_price"),
                "min_beds": filters.get("min_beds"), "max_beds": filters.get("max_beds"),
                "min_baths": filters.get("min_baths"),
                "property_types": [t for t in filters.get("property_types") or [] if t in refs.RENTAL_PROPERTY_TYPES] or None}
        raw = get_raw("/stingray/api/v1/search/rentals", _rent_params(region, data, page), label="rentals search")
        homes, matched, units = parsers.parse_rentals_search(raw if isinstance(raw, dict) else {})
        total_pages = math.ceil(matched / RENTAL_PAGE_SIZE) if isinstance(matched, int) else None
        return {"url": url, "listing": "for_rent", "location": _public_region(region), "matched_units": units,
                "results": homes,
                "pagination": {"page": page, "items_per_page": RENTAL_PAGE_SIZE, "total_pages": total_pages,
                               "total_count": matched}}
    data = {k: v for k, v in filters.items() if k not in ("sort", "status")}
    data["property_types"] = [t for t in data.get("property_types") or [] if t in refs.PROPERTY_TYPES] or None
    data["page"] = page
    sort = filters.get("sort") or ("sold_date" if sold_within else "recommended")
    if sold_within:
        result = _sale_page(region, data, status=refs.SOLD_STATUS, sort=sort, sold_within=sold_within,
                            label="sold search")
        listing = "sold"
    else:
        status = refs.SALE_STATUS[filters.get("status") or "active_and_coming_soon"]
        result = _sale_page(region, data, status=status, sort=sort if sort != "sold_date" else "recommended")
        listing = "for_sale"
    return {"url": url, "listing": listing, "filters": filters, **result}


def properties_search_url(**data):
    return cached_call("redfin.search_url", data, _search_url, ttl.SEARCH_CACHE)


# ---- property references ----------------------------------------------------------------

def _initial_info(path):
    payload = get_payload("/stingray/api/home/details/initialInfo", {"path": path, "al": 1},
                          label="property", token=True) or {}
    code = payload.get("responseCode")
    if code == 301 and payload.get("redirectUrl") and payload["redirectUrl"] != path:
        return _initial_info(payload["redirectUrl"])
    if code == 404 or not payload.get("propertyId"):
        raise RedfinNotFound("Property not found on Redfin")
    return {"property_id": payload.get("propertyId"), "listing_id": payload.get("listingId"),
            "path": path, "market": payload.get("marketName"), "has_estimate": payload.get("hasAvm"),
            "page_type": payload.get("riftPageType")}


def _resolve_property(ref):
    """refs.resolve_property output -> {property_id, listing_id, path, ...}."""
    if ref["kind"] == "address":
        rows, raw = _autocomplete_cached(ref["address"])
        row = next((r for r in rows if r.get("kind") == "address" and r.get("link")), None)
        if row is None:
            raise RedfinNotFound(f"No Redfin home matches the address {ref['address']!r}")
        path = row["link"][len(refs.SITE):]
        return _initial_info(path)
    # A bare id has no path: initialInfo resolves "/<state>/<city>/<street>/home/<id>"
    # with any placeholder segments to a 301 + the canonical path (probe
    # 2026-10-31); a bare "/home/<id>" is a 404.
    synthetic = f"/XX/Unknown/Unknown/home/{ref['property_id']}"
    path = ref.get("path") or synthetic
    try:
        return _initial_info(path)
    except RedfinNotFound:
        if path != synthetic:
            return _initial_info(synthetic)
        raise


def _property_ref(ref):
    key = {"property_id": ref.get("property_id"), "address": ref.get("address")}
    return cached_call("redfin.property_ref", key, lambda **_: _resolve_property(ref), ttl.PROPERTY_REF_CACHE)


def _ids(info):
    params = {"propertyId": info["property_id"], "accessLevel": 1, "al": 1}
    if info.get("listing_id"):
        params["listingId"] = info["listing_id"]
    return params


# ---- properties/details ------------------------------------------------------------------

# The optional sections of the dossier: (parse_property key, upstream path, needs the WAF token,
# the output fields it feeds). aboveTheFold is not here: without it there is no record.
_DETAIL_SECTIONS = (
    ("main", "/stingray/api/home/details/mainHouseInfoPanelInfo", True,
     ("description", "highlights", "listing_agents", "buying_agents", "open_houses")),
    ("below", "/stingray/api/v1/home/details/belowTheFold", False,
     ("amenities", "price_history", "public_records", "mortgage")),
    ("avm", "/stingray/api/home/details/avm", False, ("redfin_estimate",)),
    ("parcel", "/stingray/api/home/details/propertyParcelInfo", False, ("parcel",)),
    ("rent_estimate", "/stingray/api/home/details/rental-estimate", False, ("rent_estimate",)),
    ("market", "/stingray/api/home/details/marketInsightsInfo", False, ("market_competition",)),
    ("location_score", "/stingray/api/v1/home/details/location-score", False, ("location_scores",)),
    ("stats", "/stingray/api/home/details/neighborhoodStats/statsInfo", False, ("walk_scores",)),
    ("status_banner", "/stingray/api/home/details/listingStatusBannerInfo/v1", False, ("status_banner",)),
    ("neighborhood", "/stingray/api/home/details/property-neighborhood-info", False, ("address.neighborhood",)),
    ("popularity", "/stingray/api/home/details/popularityInfo", False, ("popularity",)),
    ("zoning", "/stingray/api/home/details/web/parcel-zoning", False, ("zoning",)),
    ("hot_market", "/stingray/api/home/details/hotMarketInfo", False, ("hot_market",)),
    ("sun", "/stingray/api/v1/home/details/sun-exposure", False, ("sun_exposure",)),
)


def _property_record(info):
    """The merged dossier. An optional section the site refuses (a block that
    survived the retries, e.g. a per-IP refusal on /avm) comes back null and
    its output fields are listed in `unavailable_fields`, instead of failing
    the whole record; such a record is not cached, so the next call retries."""
    ids = _ids(info)
    unavailable = []

    def optional(name, path, token, fields):
        def call():
            try:
                return get_payload_optional(path, ids, label=path.rsplit("/", 1)[-1], token=token)
            except F.RedfinUpstreamError:
                unavailable.extend(fields)
                return None
        return call

    calls = [lambda: get_payload("/stingray/api/home/details/aboveTheFold", ids, label="property", token=True)]
    calls += [optional(*section) for section in _DETAIL_SECTIONS]
    above, *rest = run_parallel(calls)
    sections = {"initial": {"propertyId": info["property_id"], "listingId": info.get("listing_id"),
                            "searchStatus": None}, "above": above}
    sections.update({section[0]: payload for section, payload in zip(_DETAIL_SECTIONS, rest)})
    record = parsers.parse_property(sections)
    record["property_id"] = record.get("property_id") or info["property_id"]
    record["listing_id"] = record.get("listing_id") or info.get("listing_id")
    record["link"] = record.get("link") or refs.home_link(info.get("path"))
    record["unavailable_fields"] = sorted(unavailable)
    return record


def _cached_record(info):
    def build(**_):
        record = _property_record(info)
        return DontCache(record) if record["unavailable_fields"] else record
    return cached_call("redfin.property", {"property_id": info["property_id"], "listing_id": info.get("listing_id")},
                       build, ttl.PROPERTY_CACHE)


def property_details(property):
    """Everything the site shows on the home page except the per-topic
    layers (estimate, schools, climate-risk, permits) which have their own
    endpoints."""
    return _cached_record(_property_ref(property))


# ---- properties/estimate -------------------------------------------------------------------

def _estimate(info):
    ids = _ids(info)
    avm, history, similars = run_parallel([
        lambda: get_payload("/stingray/api/home/details/avm", ids, label="estimate"),
        lambda: get_payload_optional("/stingray/api/home/details/avmHistoricalData", ids, label="value history"),
        lambda: get_payload_optional("/stingray/api/home/details/similars/listings", ids, label="similar listings"),
    ])
    record = parsers.parse_estimate(avm, history, similars)
    record["property_id"] = record.get("property_id") or info["property_id"]
    record["link"] = refs.home_link(info.get("path"))
    return record


def property_estimate(property):
    info = _property_ref(property)
    return cached_call("redfin.estimate", {"property_id": info["property_id"]}, lambda **_: _estimate(info),
                       ttl.ESTIMATE_CACHE)


# ---- properties/schools, climate-risk, permits ------------------------------------------------

def _facts(info, namespace, fn):
    return cached_call(namespace, {"property_id": info["property_id"]}, lambda **_: fn(info),
                       ttl.PROPERTY_FACTS_CACHE)


def _identity(info):
    return {"property_id": info["property_id"], "listing_id": info.get("listing_id"),
            "link": refs.home_link(info.get("path"))}


def _schools(info):
    payload = get_payload("/stingray/api/v1/home/details/belowTheFold/schoolsAndDistrictsInfo", _ids(info),
                          label="schools")
    return {**_identity(info), **parsers.parse_schools(payload)}


def property_schools(property):
    return _facts(_property_ref(property), "redfin.schools", _schools)


def _climate(info):
    payload = get_payload("/stingray/api/v1/home/details/belowTheFold/riskFactorData", _ids(info),
                          label="climate risk")
    return {**_identity(info), **parsers.parse_climate_risk(payload)}


def property_climate_risk(property):
    return _facts(_property_ref(property), "redfin.climate", _climate)


def _permits(info):
    ids = _ids(info)
    permits, zoning = run_parallel([
        lambda: get_payload_optional("/stingray/api/v2/home/details/permits", ids, label="permits"),
        lambda: get_payload_optional("/stingray/api/home/details/web/parcel-zoning", ids, label="zoning"),
    ])
    return {**_identity(info), **parsers.parse_permits(permits, zoning)}


def property_permits(property):
    return _facts(_property_ref(property), "redfin.permits", _permits)


# ---- rentals/details -------------------------------------------------------------------------

def _rental(property_id):
    cards = get_raw("/stingray/api/v1/rentals/homecards",
                    {"propertyIds": property_id, "includeKeyFacts": "true"}, label="rental")
    homes = (cards or {}).get("homes") if isinstance(cards, dict) else None
    if not homes:
        raise RedfinNotFound(f"No rental building with Redfin propertyId {property_id}")
    card = homes[0]
    rental_id = ((card.get("rentalExtension") or {}).get("rentalId"))
    if not rental_id:
        raise RedfinNotFound(f"Redfin propertyId {property_id} is not a rental building")
    base = f"/stingray/api/v1/rentals/{rental_id}"

    def plain(path, params=None):
        def call():
            try:
                data = get_raw(path, params, label=path.rsplit("/", 1)[-1])
            except (RedfinNotFound, RedfinBadRequest):
                return None
            return data if isinstance(data, dict) else None
        return call

    about, plans, fees, amenities, contacts, media, engagement, schools = run_parallel([
        plain(base + "/about"), plain(base + "/floorPlans", {"showPriceDrop": "true", "showPriceDropAmount": "true"}),
        plain(base + "/feesAndPolicies"), plain(base + "/amenities"), plain(base + "/contacts"),
        plain(base + "/mediaBrowserInfo"), plain(f"/stingray/api/v1/rentals/{property_id}/engagementSignals"),
        lambda: get_payload_optional("/stingray/api/rentals/schoolsAndDistricts", {"propertyId": property_id},
                                     label="rental schools"),
    ])
    return parsers.parse_rental(card, about, plans, fees, amenities, contacts, media, engagement, schools)


def rental_details(rental):
    if rental["kind"] == "address":
        info = _property_ref(rental)
        property_id = info["property_id"]
    else:
        property_id = rental["property_id"]
    return cached_call("redfin.rental", {"property_id": property_id}, lambda **_: _rental(property_id),
                       ttl.RENTAL_CACHE)


# ---- agents ----------------------------------------------------------------------------------

_AGENT_DIRECTORY_WORDS = {6: "city", 1: "neighborhood", 5: "county"}


def _agents_directory(region):
    word = _AGENT_DIRECTORY_WORDS.get(region["region_type"])
    if not word:
        raise ValueError("The agent directory covers cities, neighborhoods and counties; give one of those.")
    try:
        data = get_raw(f"/stingray/agents/data/radp/{word}/{region['region_id']}/filterable-agents-data/V3",
                       label="agent directory")
    except RedfinBadRequest:
        raise RedfinNotFound(f"No Redfin agent directory for {region['id']}")
    return parsers.parse_agent_directory(data)


def _agents_search(location, page=1):
    region = _region(location)
    agents = cached_call("redfin.agent_directory", {"region_type": region["region_type"], "region_id": region["region_id"]},
                         lambda **_: _agents_directory(region), ttl.AGENTS_CACHE)
    start = (page - 1) * AGENTS_PAGE_SIZE
    return {
        "location": _public_region(region),
        "results": agents[start:start + AGENTS_PAGE_SIZE],
        "pagination": {"page": page, "items_per_page": AGENTS_PAGE_SIZE,
                       "total_pages": max(1, math.ceil(len(agents) / AGENTS_PAGE_SIZE)), "total_count": len(agents)},
    }


def agents_search(**data):
    return _agents_search(**data)


def _agent_profile(slug):
    payload = get_payload(f"/stingray/agents/data/agent-profile/{slug}/url/get", label="agent") or {}
    if not payload.get("agentId") or payload.get("redirectUrl"):
        raise RedfinNotFound(f"No Redfin agent with slug {slug!r}")
    return payload


def _agent(slug):
    profile = _agent_profile(slug)
    base = f"/stingray/agents/data/agent-profile/{slug}"
    data = f"/stingray/agents/data/{slug}"
    rollup, stats, phone, counts, photo, sell, buy, endorsements = run_parallel([
        lambda: get_payload_optional(base + "/rollup/get", label="agent rollup"),
        lambda: get_payload_optional(data + "/agent-stats/get", label="agent stats"),
        lambda: get_payload_optional(base + "/phone/get", label="agent phone"),
        lambda: get_payload_optional(base + "/review-counts/get", label="agent reviews"),
        lambda: get_payload_optional(base + "/photo/get", label="agent photo"),
        lambda: get_payload_optional(data + "/gishomes/sell/get", label="agent listings"),
        lambda: get_payload_optional(data + "/gishomes/buy/get", label="agent listings"),
        lambda: get_payload_optional(data + "/endorsements/get", label="agent endorsements"),
    ])
    return parsers.parse_agent(profile, rollup, stats, phone, counts, photo, sell, buy, endorsements)


def agent_details(agent):
    return cached_call("redfin.agent", {"agent": agent}, lambda **_: _agent(agent), ttl.AGENTS_CACHE)


def _agent_reviews(slug):
    payload = get_payload(f"/stingray/real-estate-agents/{slug}/all-reviews", label="agent reviews")
    reviews = parsers.parse_agent_reviews(payload)
    return {"agent": slug, "link": refs.agent_link(slug), "count": len(reviews), "reviews": reviews}


def agent_reviews(agent):
    return cached_call("redfin.agent_reviews", {"agent": agent}, lambda **_: _agent_reviews(agent),
                       ttl.AGENT_REVIEWS_CACHE)


# ---- markets ---------------------------------------------------------------------------------

def _market_overview(region):
    rt, rid = region["region_type"], region["region_id"]
    insights, trends, compete, prices, demand, affordability, climate, walk, nearby = run_parallel([
        lambda: get_payload_optional("/stingray/api/market-insights-page", {"region_id": rid, "region_type": rt},
                                     label="market insights"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/1/trends", label="market trends"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/compete-score", label="compete score"),
        lambda: get_payload_optional(f"/stingray/api/graph/{rt}/{rid}/All/regional-housing-market/home_prices",
                                     label="price history"),
        lambda: get_payload_optional(f"/stingray/api/graph/{rt}/{rid}/All/regional-housing-market/demand",
                                     label="demand history"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/1/affordability", label="affordability"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/climate-data", label="climate"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/1/walk-score-data", label="walk score"),
        lambda: get_payload_optional(f"/stingray/api/region/{rt}/{rid}/nearby-compete-scores", label="nearby"),
    ])
    record = parsers.parse_market_overview(insights, trends, compete, prices, demand, affordability, climate,
                                           walk, nearby)
    public = _public_region(region)
    record["region"] = {**record["region"], **{k: v for k, v in public.items() if v is not None}}
    return record


def _market_cached(location, namespace, fn):
    region = _region(location)
    return cached_call(namespace, {"region_type": region["region_type"], "region_id": region["region_id"]},
                       lambda **_: fn(region), ttl.MARKET_CACHE)


def market_overview(location):
    return _market_cached(location, "redfin.market", _market_overview)


def _recently_sold(region):
    payload = get_payload("/stingray/api/gis/recently-sold",
                          {"region_type": region["region_type"], "region_id": region["region_id"]},
                          label="recently sold")
    record = parsers.parse_recently_sold(payload)
    return {"location": _public_region(region), "count": len(record["homes"]), "link": record["link"],
            "results": record["homes"]}


def market_recently_sold(location):
    return _market_cached(location, "redfin.market_sold", _recently_sold)


def _best_schools(region):
    payload = get_payload(f"/stingray/api/region/{region['region_type']}/{region['region_id']}/1/best-schools",
                          label="best schools")
    return {"location": _public_region(region), **parsers.parse_best_schools(payload)}


def market_schools(location):
    return _market_cached(location, "redfin.market_schools", _best_schools)
