"""Offline tests for the /redfin/* request schemas and the reference
resolvers behind them.

    python -m pytest redfin/test_schemas.py -q
"""
import os
import sys

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redfin import refs, schemas  # noqa: E402


# ---- location ------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("city/30818", {"kind": "region", "region_type": 6, "region_id": 30818}),
    ("City/30818", {"kind": "region", "region_type": 6, "region_id": 30818}),
    ("neighborhood/165450", {"kind": "region", "region_type": 1, "region_id": 165450}),
    ("county/2866", {"kind": "region", "region_type": 5, "region_id": 2866}),
    ("school/135664", {"kind": "region", "region_type": 7, "region_id": 135664}),
    ("school-district/5816", {"kind": "region", "region_type": 8, "region_id": 5816}),
    ("state/36", {"kind": "region", "region_type": 4, "region_id": 36}),
    ("https://www.redfin.com/city/30818/TX/Austin", {"kind": "region", "region_type": 6, "region_id": 30818}),
    ("https://www.redfin.com/city/30818/TX/Austin/filter/min-beds=3,sort=lo-days",
     {"kind": "region", "region_type": 6, "region_id": 30818}),
    ("https://www.redfin.com/city/30818/TX/Austin/apartments-for-rent",
     {"kind": "region", "region_type": 6, "region_id": 30818}),
    ("https://www.redfin.com/neighborhood/165450/TX/Austin/Allandale/housing-market",
     {"kind": "region", "region_type": 1, "region_id": 165450}),
    ("https://www.redfin.com/city/35948/NY/Manhattan/real-estate/agents",
     {"kind": "region", "region_type": 6, "region_id": 35948}),
    ("redfin.com/county/2866/TX/Travis-County", {"kind": "region", "region_type": 5, "region_id": 2866}),
])
def test_resolve_location_regions(value, expected):
    got = refs.resolve_location(value)
    assert {k: got[k] for k in expected} == expected


def test_resolve_location_queries():
    assert refs.resolve_location("Austin, TX")["kind"] == "query"
    assert refs.resolve_location("78704") == {"kind": "query", "query": "78704", "filters": None, "is_rentals": False}
    assert refs.resolve_location("zipcode/78704")["query"] == "78704"
    assert refs.resolve_location("https://www.redfin.com/zipcode/78704")["query"] == "78704"
    assert refs.resolve_location("https://www.redfin.com/state/Texas")["query"] == "Texas"


def test_resolve_location_keeps_filters_and_rentals():
    got = refs.resolve_location("https://www.redfin.com/city/30818/TX/Austin/apartments-for-rent/filter/min-price=1k")
    assert got["is_rentals"] is True and got["filters"] == "min-price=1k"
    got = refs.resolve_location("https://www.redfin.com/zipcode/78704/filter/min-beds=3/page-2")
    assert got["filters"] == "min-beds=3"


@pytest.mark.parametrize("value", ["", "https://example.com/city/30818", "https://www.redfin.com/TX/Austin",
                                   "https://www.redfin.com/zipcode/787", "city/abc", "foo/30818"])
def test_resolve_location_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_location(value)


def test_search_sale_schema_defaults():
    loaded = schemas.SearchSaleSchema().load({"location": "city/30818"})
    assert loaded["location"]["region_id"] == 30818
    assert loaded["status"] == "active_and_coming_soon" and loaded["sort"] == "recommended" and loaded["page"] == 1
    assert loaded["property_types"] is None and loaded["min_price"] is None


def test_search_sale_schema_filters():
    loaded = schemas.SearchSaleSchema().load({
        "location": "Austin, TX", "min_price": "400000", "max_price": "800000", "min_beds": "3",
        "property_types": "House, condo", "status": "Active", "sort": "price_low", "page": "2"})
    assert loaded["property_types"] == ["house", "condo"]
    assert loaded["min_price"] == 400000.0 and loaded["status"] == "active" and loaded["page"] == 2


@pytest.mark.parametrize("query,field", [
    ({"location": "city/30818", "min_price": "900000", "max_price": "100"}, "max_price"),
    ({"location": "city/30818", "property_types": "castle"}, "property_types"),
    ({"location": "city/30818", "status": "sold"}, "status"),
    ({"location": "city/30818", "page": "0"}, "page"),
    ({"location": "city/30818", "url": "x"}, "url"),
    ({}, "location"),
])
def test_search_sale_schema_rejects(query, field):
    with pytest.raises(ValidationError) as info:
        schemas.SearchSaleSchema().load(query)
    assert field in info.value.messages


def test_search_sold_schema():
    loaded = schemas.SearchSoldSchema().load({"location": "city/16163", "sold_within": "180"})
    assert loaded["sold_within"] == 180 and loaded["sort"] == "sold_date"
    with pytest.raises(ValidationError):
        schemas.SearchSoldSchema().load({"location": "city/16163", "sold_within": "45"})


def test_search_rent_schema():
    loaded = schemas.SearchRentSchema().load({"location": "city/30818", "min_rent": "1000", "max_rent": "2500",
                                              "property_types": "apartment,house", "sort": "price_high"})
    assert loaded["property_types"] == ["apartment", "house"] and loaded["max_rent"] == 2500.0
    with pytest.raises(ValidationError):
        schemas.SearchRentSchema().load({"location": "city/30818", "min_rent": "3000", "max_rent": "1000"})
    with pytest.raises(ValidationError):
        schemas.SearchRentSchema().load({"location": "city/30818", "property_types": "land"})


def test_search_coordinates_schema_polygon_and_circle():
    loaded = schemas.SearchCoordinatesSchema().load({"area": "-97.80 30.25, -97.70 30.25, -97.70 30.32"})
    assert loaded["area"].count(",") == 3            # closed: 4 points
    assert loaded["listing"] == "for_sale"
    circle = schemas.SearchCoordinatesSchema().load({"area": "30.27,-97.74,3", "listing": "sold"})
    assert circle["area"].count(",") == 24 and circle["listing"] == "sold"
    for bad in ("", "1 2", "a b, c d, e f", "30.27,-97.74,99", "0 91, 0 0, 1 1"):
        with pytest.raises(ValidationError):
            schemas.SearchCoordinatesSchema().load({"area": bad})


def test_search_url_schema():
    url = "https://www.redfin.com/city/30818/TX/Austin/filter/min-price=400k,max-price=800k"
    assert schemas.SearchUrlSchema().load({"url": url}) == {"url": url, "page": 1}
    for bad in ("city/30818", "https://www.zillow.com/austin-tx/", "https://www.redfin.com/TX/Austin/x/home/1"):
        with pytest.raises(ValidationError):
            schemas.SearchUrlSchema().load({"url": bad})


def test_parse_filter_string():
    params, sold = refs.parse_filter_string(
        "sort=lo-days,property-type=house+condo,min-price=400k,max-price=800k,min-beds=3,max-beds=5,min-baths=2,"
        "min-sqft=1.5k-sqft,max-sqft=3k-sqft,min-year-built=1990,max-year-built=2020,min-lot-size=4.5k-sqft,"
        "max-lot-size=1-acre,hoa=200,min-stories=1,max-stories=2,status=active")
    assert params == {"sort": "newest", "property_types": "house,condo", "min_price": 400000, "max_price": 800000,
                      "min_beds": 3, "max_beds": 5, "min_baths": 2, "min_sqft": 1500, "max_sqft": 3000,
                      "min_year_built": 1990, "max_year_built": 2020, "min_lot_sqft": 4500, "max_lot_sqft": 43560,
                      "max_hoa": 200, "min_stories": 1, "max_stories": 2, "status": "active"}
    assert sold is None
    params, sold = refs.parse_filter_string("include=sold-6mo,sort=hi-sale-date,min-price=300k,waterfront")
    assert sold == 180 and params == {"sort": "sold_date", "min_price": 300000}
    params, _ = refs.parse_filter_string("min-baths=1.5,max-beds=x")
    assert params == {"min_baths": 1.5}


# ---- property / rental / agent ---------------------------------------------------------

def test_property_schema_accepts_id_link_and_address():
    assert schemas.PropertySchema().load({"property": "31466150"})["property"] == \
        {"kind": "id", "property_id": 31466150, "path": None}
    loaded = schemas.PropertySchema().load(
        {"property": "https://www.redfin.com/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150"})
    assert loaded["property"] == {"kind": "id", "property_id": 31466150,
                                  "path": "/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150"}
    loaded = schemas.PropertySchema().load({"property": "6005 Shoalwood Ave, Austin, TX 78757"})
    assert loaded["property"] == {"kind": "address", "address": "6005 Shoalwood Ave, Austin, TX 78757"}


@pytest.mark.parametrize("value", ["", "austin", "https://www.redfin.com/city/30818/TX/Austin",
                                   "https://www.zillow.com/homedetails/1_zpid/", "12345678901234"])
def test_property_schema_rejects(value):
    with pytest.raises(ValidationError):
        schemas.PropertySchema().load({"property": value})


def test_property_schema_rejects_sibling_params():
    with pytest.raises(ValidationError):
        schemas.PropertySchema().load({"property": "31466150", "url": "https://www.redfin.com/x/home/1"})


def test_rental_schema():
    assert schemas.RentalSchema().load({"rental": "31632468"})["rental"]["property_id"] == 31632468
    loaded = schemas.RentalSchema().load({"rental": "https://www.redfin.com/TX/Austin/2239-Cromwell-Cir-78741/home/31632468"})
    assert loaded["rental"]["property_id"] == 31632468
    loaded = schemas.RentalSchema().load({"rental": "https://www.redfin.com/CA/Santa-Monica/700-Broadway-90401/apartment/199518686"})
    assert loaded["rental"]["property_id"] == 199518686
    with pytest.raises(ValidationError):
        schemas.RentalSchema().load({})


def test_agent_schema():
    assert schemas.AgentSchema().load({"agent": "David-Tom"}) == {"agent": "david-tom"}
    assert schemas.AgentSchema().load({"agent": "https://www.redfin.com/real-estate-agents/david-tom/reviews"}) == \
        {"agent": "david-tom"}
    for bad in ("", "david tom", "https://www.redfin.com/real-estate-agents", "https://example.com/real-estate-agents/x"):
        with pytest.raises(ValidationError):
            schemas.AgentSchema().load({"agent": bad})


def test_agents_search_and_market_schemas():
    loaded = schemas.AgentsSearchSchema().load({"location": "https://www.redfin.com/city/35948/NY/Manhattan"})
    assert loaded["location"]["region_id"] == 35948 and loaded["page"] == 1
    assert schemas.MarketSchema().load({"location": "Allandale Austin"})["location"]["kind"] == "query"
    assert schemas.LocationsSearchSchema().load({"query": " 78704 "}) == {"query": "78704"}
    with pytest.raises(ValidationError):
        schemas.LocationsSearchSchema().load({"query": ""})


def test_region_id_helpers():
    assert refs.region_id(6, 30818) == "city/30818"
    assert refs.region_id(2, 34098) == "zipcode/34098"
    assert refs.region_id(99, 1) is None
    assert refs.filter_number("1-acre") == 43560 and refs.filter_number("2.5m") == 2500000
    assert refs.filter_number("abc") is None
