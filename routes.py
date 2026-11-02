"""The 18 Redfin endpoints. Every path is served with and without the
`/redfin` prefix, so code generated against the hosted API on RapidAPI
(paths like /properties/details) runs unchanged against this server.

Params are validated by the marshmallow schemas in redfin/schemas.py (ONE
param per input: `location` takes a place name, ZIP, region id like
city/1669 or a redfin.com region link; `property` a property id, a home link
or a street address; `rental` a property id or link; `agent` a slug or
profile link); unknown params are rejected."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from redfin import api, schemas


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so getunicode is mandatory)."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "", False)}
    query["page"] = page
    scheme, host = request.urlparts.scheme, request.urlparts.netloc
    return f"{scheme}://{host}{path}?{urlencode(query, doseq=True)}"


def paginate(result, path, params):
    """Lift the `pagination` block into the flat shape the hosted API
    returns: count / per_page / current_page / total_pages / next / previous."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)   # per-page count; `count` is the total
    page = _as_int(pagination.get("page")) or _as_int(params.get("page")) or 1
    if "has_next" in pagination:
        has_next = bool(pagination["has_next"])
        total_pages = pagination.get("total_pages")
        if total_pages is not None:
            total_pages = _as_int(total_pages, None)
    else:
        total_pages = max(_as_int(pagination.get("total_pages")), 0)
        has_next = page < total_pages
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if has_next else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema_cls, impl, paginated=False):
    """Validate, run, map errors: bad params -> 400, missing entity -> 404,
    blocks / transport -> 502, anything else -> 500."""
    raw = query_dict()
    data, error = load_query(schema_cls, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"redfin rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"redfin blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"redfin {label} failed: {e}"}, 502)
    except Exception as e:                 # parser surprises
        return json_response({"error": f"redfin {label} failed: {type(e).__name__}: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


ENDPOINTS = [
    ("/properties/details", schemas.PropertySchema, api.property_details, False),
    ("/locations/search", schemas.LocationsSearchSchema, api.locations_search, False),
    ("/properties/search-sale", schemas.SearchSaleSchema, api.properties_search_sale, True),
    ("/properties/search-sold", schemas.SearchSoldSchema, api.properties_search_sold, True),
    ("/properties/search-rent", schemas.SearchRentSchema, api.properties_search_rent, True),
    ("/properties/search-coordinates", schemas.SearchCoordinatesSchema, api.properties_search_coordinates, True),
    ("/properties/search-url", schemas.SearchUrlSchema, api.properties_search_url, True),
    ("/properties/estimate", schemas.PropertySchema, api.property_estimate, False),
    ("/properties/schools", schemas.PropertySchema, api.property_schools, False),
    ("/properties/climate-risk", schemas.PropertySchema, api.property_climate_risk, False),
    ("/properties/permits", schemas.PropertySchema, api.property_permits, False),
    ("/rentals/details", schemas.RentalSchema, api.rental_details, False),
    ("/agents/search", schemas.AgentsSearchSchema, api.agents_search, True),
    ("/agents/details", schemas.AgentSchema, api.agent_details, False),
    ("/agents/reviews", schemas.AgentSchema, api.agent_reviews, False),
    ("/markets/overview", schemas.MarketSchema, api.market_overview, False),
    ("/markets/recently-sold", schemas.MarketSchema, api.market_recently_sold, False),
    ("/markets/schools", schemas.MarketSchema, api.market_schools, False),
]


def mount(path, schema_cls, impl, paginated):
    """Serve a handler at /path and /redfin/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema_cls, impl, paginated)

    handler.__name__ = "redfin_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/redfin" + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
