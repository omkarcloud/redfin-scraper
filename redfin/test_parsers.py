"""Offline tests for the Redfin parsers against the captured fixtures
(redfin/fixtures/, 2026-10-31): happy paths, sparse / off-market records,
empty pages and the site's error envelopes.

    python -m pytest redfin/test_parsers.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from redfin import parsers as P  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FX, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def payload(name):
    return fixture(name).get("payload")


def sections(tag):
    def section(name):
        path = os.path.join(FX, f"property_{tag}_{name}.json")
        return payload(f"property_{tag}_{name}") if os.path.exists(path) else None
    return {
        "initial": section("initialInfo"), "above": section("aboveTheFold"),
        "main": section("mainHouseInfoPanelInfo"), "below": section("belowTheFold"), "avm": section("avm"),
        "parcel": section("propertyParcelInfo"), "rent_estimate": section("rental-estimate"),
        "market": section("marketInsightsInfo"), "location_score": section("location-score"),
        "stats": section("neighborhoodStats_statsInfo"), "status_banner": section("listingStatusBannerInfo_v1"),
        "neighborhood": section("property-neighborhood-info"), "popularity": section("popularityInfo"),
        "zoning": section("web_parcel-zoning"), "hot_market": section("hotMarketInfo"), "sun": section("sun-exposure"),
    }


# ---- value helpers -------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("$758K", 758000), ("95.7%", 95.7), ("+1.9%", 1.9), ("-6.1 pt", -6.1), ("$1.2M", 1200000),
    ("1,404", 1404), ("46", 46), (750000, 750000), ("", None), ("n/a", None), (None, None), (True, None),
])
def test_number(value, expected):
    assert P._number(value) == expected


def test_dates():
    assert P._date_ms(1793257200000) == "2026-10-29"      # Central-midnight stamp
    assert P._date_ms(1572505200000) == "2019-10-31"      # Pacific-midnight stamp
    assert P._date_ms(None) is None and P._date_ms("x") is None
    assert P._datetime_ms(1793473200000) == "2026-10-31T19:00:00Z"
    assert P._iso("2026-10-28T20:32:58.346339Z") == "2026-10-28T20:32:58Z"
    assert P._days(68741038) == 0.8


def test_photo_links_follow_the_site_names():
    links = P._photo_links(92, "5861141", "0:2,1-3:0")
    assert links == ["https://ssl.cdn-redfin.com/photo/92/bigphoto/141/5861141_0_2.jpg",
                     "https://ssl.cdn-redfin.com/photo/92/bigphoto/141/5861141_1_0.jpg",
                     "https://ssl.cdn-redfin.com/photo/92/bigphoto/141/5861141_2_0.jpg",
                     "https://ssl.cdn-redfin.com/photo/92/bigphoto/141/5861141_3_0.jpg"]
    assert P._photo_links(641, "2227163694299134929", "0-1:0")[0].endswith("/929/2227163694299134929_0.jpg")
    assert P._photo_links(None, "1", "0-1:0") == [] and P._photo_links(1, None, "0:0") == []


# ---- locations -----------------------------------------------------------------------

def test_autocomplete_rows():
    rows = P.parse_autocomplete(payload("autocomplete_austin"))
    first = rows[0]
    assert first["id"] == "city/30818" and first["kind"] == "city" and first["region_type"] == "city"
    assert first["link"] == "https://www.redfin.com/city/30818/TX/Austin" and first["region"] == "Austin, TX, USA"
    kinds = {r["kind"] for r in rows}
    assert {"city", "neighborhood", "school_district", "agents", "building"} <= kinds
    agents = [r for r in rows if r["kind"] == "agents"]
    assert agents and all(r["link"].endswith("/real-estate/agents") for r in agents)
    assert all(r["id"].startswith(("city/", "neighborhood/")) for r in agents)


def test_autocomplete_zip_address_and_no_match():
    zip_rows = P.parse_autocomplete(payload("autocomplete_zip"))
    assert zip_rows[0] == {**zip_rows[0], "id": "zipcode/78704", "kind": "zipcode", "name": "78704",
                           "link": "https://www.redfin.com/zipcode/78704", "region_type": "zipcode"}
    address = P.parse_autocomplete(payload("autocomplete_address"))[0]
    assert address["kind"] == "address" and address["id"] == 31466150
    assert address["link"] == "https://www.redfin.com/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150"
    assert P.parse_autocomplete(payload("autocomplete_nomatch")) == []
    assert P.parse_autocomplete(None) == []


def test_breadcrumbs():
    crumbs = P.parse_breadcrumbs(payload("region_breadcrumbs_city"))
    assert crumbs["name"] == "Austin" and crumbs["link"] == "https://www.redfin.com/city/30818/TX/Austin"
    assert crumbs["trail"][0]["name"] == "Redfin"
    assert fixture("region_breadcrumbs_bad")["resultCode"] == 101     # -> NotFound in fetch.get_payload


# ---- search pages --------------------------------------------------------------------

def test_search_sale_page():
    homes, median = P.parse_search(payload("gis_sale_austin"))
    assert len(homes) == 6 and median["price"] > 0 and median["beds"]
    home = homes[0]
    assert home["property_id"] == 31216168 and home["listing_id"] == 224959522
    assert home["mls_id"] == "2227892766810728689"
    assert home["link"] == "https://www.redfin.com/TX/West-Lake-Hills/429-Ridgewood-Rd-78746/home/31216168"
    assert home["status"] == "Coming Soon" and home["search_status"] == "coming_soon" and home["property_type"] == "house"
    assert home["price"] == 1475000 and home["price_per_sqft"] == 854 and home["sqft"] == 1727 and home["lot_sqft"] == 14374
    assert home["address"]["city"] == "West Lake Hills" and home["address"]["neighborhood"] == "Westlake"
    assert home["address"]["latitude"] == pytest.approx(30.2777356)
    assert home["photos"]["count"] == 40 and len(home["photos"]["links"]) == 40
    assert home["photos"]["primary"].endswith("/photo/641/bigphoto/689/2227892766810728689_0.jpg")
    assert home["photos"]["links"][1].endswith("/2227892766810728689_1_0.jpg")
    assert home["remarks"].startswith("Discover a rare opportunity")
    assert home["hoa_monthly"] is None and home["sold_date"] is None and home["open_house"] is None
    assert "pool_type" not in home and "dataSourceId" not in home
    with_hoa = [h for h in homes if h["hoa_monthly"] is not None]
    assert all(isinstance(h["hoa_monthly"], (int, float)) for h in with_hoa)


def test_search_sold_page():
    homes, _ = P.parse_search(payload("gis_sold_seattle"))
    closed = [h for h in homes if h["status"] == "Closed"]
    assert closed and closed[0]["search_status"] == "sold" and closed[0]["sold_date"]
    assert closed[0]["price"] > 0 and closed[0]["is_price_hidden"] is False


def test_search_empty_page():
    assert P.parse_search(payload("gis_empty")) == ([], None)
    assert P.parse_search(None) == ([], None)
    assert P.parse_home(None) is None


def test_rentals_search():
    homes, matched, units = P.parse_rentals_search(fixture("rentals_search_austin"))
    assert len(homes) == 6 and matched > 1000 and units > matched
    home = homes[0]
    assert home["rental_id"] and home["name"] == "Wixby House" and home["property_type"] == "apartment"
    assert home["rent"] == {"min": 1170, "max": 2350, "currency": "USD"}
    assert home["beds"]["min"] == 0 and home["available_units"] == 307
    assert home["address"]["city"] == "Austin" and home["address"]["latitude"] == pytest.approx(30.154448)
    assert home["listed_at"] == "2026-10-29T04:20:44Z" and home["photo_count"] == 78
    assert P.parse_rentals_search({}) == ([], None, None)


# ---- property details ----------------------------------------------------------------

def test_property_active_listing():
    record = P.parse_property(sections("austin"))
    assert record["property_id"] == 31466150 and record["listing_id"] == 224889714 and record["mls_id"] == "5861141"
    assert record["link"] == "https://www.redfin.com/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150"
    assert record["status"] == "Active" and record["search_status"] == "active" and record["is_off_market"] is False
    assert record["listing_source"] == "Unlock MLS" and record["property_type"] == "house"
    assert record["price"] == 750000 and record["price_per_sqft"] == 534
    assert record["beds"] == 3 and record["baths"] == 2.0 and record["sqft"] == 1404 and record["lot_sqft"] == 14675
    assert record["year_built"] == 1952 and record["stories"] == 1.0
    address = record["address"]
    assert address["street"] == "6005 Shoalwood Ave" and address["unit"] is None and address["zip"] == "78757"
    assert address["county"] == "Travis County" and address["neighborhood"] == "Allandale"
    assert record["description"].startswith("Fantastic opportunity in the heart of Allandale!")
    assert {"name": "Lot Size", "value": "0.34 acres"} in record["highlights"]
    assert record["listing_agents"] == [{"name": "Kathryn Dochen", "broker": "Bramlett Partners", "license": None,
                                         "is_redfin_agent": False, "is_partner_agent": False}]
    assert record["listing_contact_phone"] == "(512) 850-5717"
    assert record["photos"]["count"] == 40 and record["photos"]["links"][1].endswith("5861141_1_0.jpg")
    assert record["photos"]["interactive_floorplan_link"].startswith("https://visithome.ai/")
    assert record["amenities_count"] == 64
    categories = {a["category"] for a in record["amenities"]}
    assert {"Parking", "Interior", "Exterior", "Financial", "Utilities"} <= categories
    parking = next(a for a in record["amenities"] if a["category"] == "Parking")
    garage = parking["sections"][0]["facts"]
    assert {"name": "Garage", "values": ["Has Garage"]} in garage          # GARAGE_YN, no label on the site
    assert {"name": "# of Garage Spaces", "values": ["1"]} in garage       # the site's own label wins
    assert not any("_" in f["name"] for a in record["amenities"] for s in a["sections"] for f in s["facts"])
    public_facts = next(a for a in record["amenities"] if a["category"] == "Public facts")
    assert public_facts["sections"][0]["title"] is None and public_facts["sections"][0]["facts"]
    assert record["price_history"][0] == {"date": "2026-10-29", "event": "Listed", "mls_status": "Active",
                                          "price": 750000, "is_price_hidden": False, "appreciation": None,
                                          "source": "Unlock MLS", "source_listing_id": "5861141", "note": None}
    hidden = record["price_history"][1]
    assert hidden["price"] is None and hidden["is_price_hidden"] is True and "non-disclosure" in hidden["note"]
    assert record["public_records"]["tax"] == {"roll_year": 2026, "taxes_due": 13921.73, "land_value": 474164,
                                               "improvement_value": 206111}
    assert len(record["public_records"]["tax_history"]) >= 5 and record["public_records"]["apn"] == "231646"
    assert record["mortgage"]["rates"]["thirty_year_fixed"] > 0 and record["mortgage"]["property_tax_rate_percent"]
    assert record["redfin_estimate"]["is_available"] is False and record["redfin_estimate"]["value"] is None
    assert record["rent_estimate"] is None                     # "Estimate Not Available"
    assert record["market_competition"]["region"] == "Allandale" and record["market_competition"]["compete_score"] == 61
    assert record["walk_scores"]["walk_score"] == 81 and record["walk_scores"]["walk_score_label"] == "Very Walkable"
    assert record["location_scores"]["groceries"] == pytest.approx(9.03, abs=0.01)
    assert record["parcel"]["apn"] == "231646" and record["parcel"]["fips"] == "48453"
    assert record["zoning"][0]["code"] == "SF-2" and record["zoning"][0]["jurisdiction"] == "AUSTIN CITY"
    assert record["popularity"]["is_popular"] in (True, False)
    assert record["status_banner"] and record["timezone"] == "US/Central"
    assert record["last_updated"] == "2026-10-30T09:02:07Z"


def test_property_coming_soon_with_estimate():
    record = P.parse_property(sections("seattle"))
    assert record["status"] == "Coming Soon" and record["search_status"] == "coming_soon"
    assert record["redfin_estimate"]["value"] == pytest.approx(979751.78)
    assert record["redfin_estimate"]["last_sold_price"] == 670000 and record["redfin_estimate"]["last_sold_date"] == "2019-10-31"
    assert record["rent_estimate"] == {"value": 3887, "low": 3790, "high": 3944, "currency": "USD"}
    assert record["price_history"] == []                       # NWMLS hides the timeline
    assert record["public_records"]["beds"] == 3 and record["public_records"]["county"] == "King County"
    assert record["address"]["neighborhood"] == "Maple Leaf"


def test_property_off_market_public_record():
    record = P.parse_property(sections("offmarket"))
    assert record["property_id"] == 1210666 and record["status"] == "Sold" and record["is_off_market"] is True
    assert record["search_status"] == "off_market" and record["listing_source"] == "Greater Lansing AoR"
    assert record["price"] == 6750000 and record["price_label"] == "Last Sold Price"
    assert record["listing_agents"][0]["license"] == "01733750"
    assert record["photos"]["count"] == 1 and len(record["photos"]["links"]) == 1
    assert len(record["price_history"]) == 15 and record["price_history"][0]["event"] == "Sold (MLS)"
    assert record["open_houses"] == [] and record["listing_contact_phone"] is None


def test_property_tolerates_missing_sections():
    record = P.parse_property({"above": payload("property_austin_aboveTheFold")})
    assert record["property_id"] == 31466150 and record["price"] == 750000 and record["amenities"] == []
    assert record["public_records"] is None and record["market_competition"] is None and record["zoning"] is None
    assert P.parse_property({})["property_id"] is None


def test_estimate_with_history_and_comparables():
    record = P.parse_estimate(payload("property_seattle_avm"), payload("property_seattle_avmHistoricalData"),
                              payload("property_seattle_similars_listings"))
    assert record["redfin_estimate"] == pytest.approx(979751.78) and record["is_available"] is True
    assert record["listing_price"] == 975000 and record["last_sold_price"] == 670000
    assert len(record["comparables"]) == 5
    comp = record["comparables"][0]
    assert comp["status"] == "Closed" and comp["price"] == 780000 and comp["sold_date"] == "2026-09-30"
    assert comp["address"]["street"] == "8258 2nd Ave NE" and comp["link"].endswith("/home/108468")
    history = record["value_history"]
    assert history and len(history["property"]) == 60 and history["property"][-1]["value"] == 979752
    assert history["property"][-1]["month"] == "2026-11" and history["property"][0]["month"] == "2021-12"
    assert len(history["zip"]) == 60 and len(history["county"]) == 60
    assert record["similar_listings"]["homes"] and record["similar_listings"]["min_price"] > 0


def test_estimate_unavailable():
    record = P.parse_estimate(payload("property_austin_avm"), payload("property_austin_avmHistoricalData"), None)
    assert record["is_available"] is False and record["redfin_estimate"] is None
    assert record["value_history"] is None and record["similar_listings"] is None
    assert P.parse_estimate(None, None, None)["is_available"] is False


def test_schools():
    record = P.parse_schools(payload("property_austin_belowTheFold_schoolsAndDistrictsInfo"))
    serving = record["schools_serving_home"]
    assert serving[0]["name"] == "Gullett Elementary School" and serving[0]["rating"] == 10
    assert serving[0]["levels"] == ["elementary"] and serving[0]["distance_miles"] == 0.6
    assert serving[0]["district"]["name"] == "Austin Independent School District"
    assert serving[0]["reviews"][0]["rating"] in (1, 2, 3, 4, 5)
    assert serving[0]["sub_ratings"][0]["type"]
    assert record["nearby_schools"] and all(s["id"] not in {x["id"] for x in serving} for s in record["nearby_schools"])
    assert record["districts"][0]["name"]
    assert P.parse_schools(None) == {"schools_serving_home": [], "nearby_schools": [], "districts": [],
                                     "summary": None}


def test_climate_risk():
    record = P.parse_climate_risk(payload("property_austin_belowTheFold_riskFactorData"))
    assert record["flood"]["factor"] == 1 and record["flood"]["fema_zones"] == ["X (unshaded)"]
    assert record["flood"]["risk_direction"] == "stationary" and record["flood"]["insurance_low"] == 507
    assert record["fire"]["factor"] == 1 and record["fire"]["risk_direction"] == "stationary"
    assert record["heat"]["factor"] == 8 and record["heat"]["hot_days_in_30_years"] == 23
    assert record["wind"]["factor"] == 6 and record["air"]["factor"] == 2
    assert record["report_link"].startswith("https://firststreet.org/")
    empty = P.parse_climate_risk(None)
    assert empty["flood"] is None and empty["air"] is None


def test_permits_and_zoning():
    record = P.parse_permits(payload("property_austin_permits"), payload("property_austin_web_parcel-zoning"))
    assert record["permits_count"] == len(record["permits"]) >= 2
    newest = record["permits"][0]
    assert newest["number"] and newest["status"] == "final" and newest["file_date"] >= record["permits"][1]["file_date"]
    assert newest["summary"] and newest["tags"]
    assert record["zoning"][0]["code"] == "SF-2" and record["zoning"][0]["ordinance_date"] == "2024-12-03"
    assert P.parse_permits(None, None) == {"permits": [], "permits_count": 0, "zoning": []}


# ---- rentals -------------------------------------------------------------------------

def test_rental_building():
    record = P.parse_rental(fixture("rental_homecards")["homes"][0], fixture("rental_about"),
                            fixture("rental_floorPlans"), fixture("rental_feesAndPolicies"),
                            fixture("rental_amenities"), fixture("rental_contacts"), fixture("rental_mediaBrowserInfo"),
                            fixture("rental_engagementSignals"), payload("rental_schoolsAndDistricts"))
    assert record["property_id"] == 31632468 and record["rental_id"] == "4ebacff4-5a06-46a4-a5e9-72883beb4db0"
    assert record["name"] == "Patten East" and record["management_company"] == "Ryse Management"
    assert record["phone"] == "7374092500" and len(record["leasing_office_hours"]) == 6
    assert record["floor_plans_count"] == 6 and record["total_units"] == 14
    plan = record["floor_plans"][0]
    assert plan["name"] == "1x1E" and plan["beds"] == 1 and plan["rent"]["min"] == 839 and plan["units"][0]["rent"] == 840
    assert record["unit_rent"]["min"] >= 694 and record["unit_rent"]["max"] <= 1528
    assert record["fees"]["application"] == 60 and record["fees"]["admin"] == 175
    assert record["pet_policies"][0]["pet"] == "cats" and record["pet_policies"][0]["fee"] == 400
    assert record["parking"][0]["type"] == "Surface Lot"
    assert "Fireplace" in record["amenities"]["unit"] and record["engagement"]["views"] == 69
    assert record["schools"]["schools_serving_home"]
    assert record["description"].startswith("Discover Convenient Living")


def test_rental_building_sparse():
    record = P.parse_rental(fixture("rental_homecards")["homes"][0], None, None, None, None, None, None, None, None)
    assert record["name"] == "Patten East" and record["floor_plans"] == [] and record["fees"] is None
    assert record["amenities"] is None and record["schools"] is None and record["engagement"] is None
    assert fixture("rental_homecards_empty") == {"homes": []}


# ---- agents --------------------------------------------------------------------------

def test_agent_directory():
    agents = P.parse_agent_directory(fixture("agents_directory_city"))
    assert len(agents) == 141
    agent = agents[0]
    assert agent["slug"] == "peter-forcey" and agent["email"] == "peter.forcey@redfin.com"
    assert agent["phone"] == "(646) 362-9137" and agent["brokerage"] == "Redfin Real Estate"
    assert agent["link"] == "https://www.redfin.com/real-estate-agents/peter-forcey"
    assert agent["deals_past_year"] == 7 and agent["volume_lifetime"] == 30059000
    assert agent["service_regions"] == ["South Manhattan", "North Manhattan", "Manhattan"]
    assert P.parse_agent_directory({}) == []


def test_agent_profile():
    record = P.parse_agent(payload("agent_url"), payload("agent_rollup"), payload("agent_agent-stats"),
                           payload("agent_phone"), payload("agent_review-counts"), payload("agent_photo"),
                           payload("agent_gishomes_sell"), payload("agent_gishomes_buy"), payload("agent_endorsements"))
    assert record["id"] == 49684 and record["slug"] == "david-tom" and record["name"] == "David Tom"
    assert record["email"] == "david.tom@redfin.com" and record["phone"] == "(347) 618-3591"
    assert record["license_number"] == "10301220329" and record["started_year"] == 2022
    assert record["photo"] == "https://ssl.cdn-redfin.com/system_files/images/49684/500x500/8_17.jpg"
    assert record["rating"] == 9.44 and record["rating_out_of_5"] == 4.7 and record["review_count"] == 18
    assert record["review_breakdown"]["five_star"] == 15
    assert record["service_regions"][0] == {"id": "city/35948", "name": "Manhattan",
                                            "link": "https://www.redfin.com/city/35948/NY/Manhattan"}
    assert record["team"][0]["role"] == "Transaction Coordinator"
    assert record["stats"]["transactions_lifetime"] == 42 and record["stats"]["volume_lifetime"] == 39064500
    assert record["active_listings"] == [] and record["is_premier"] is True
    assert payload("agent_notfound_url") == {"redirectUrl": "/real-estate-agents"}


def test_agent_profile_sparse():
    record = P.parse_agent(payload("agent_url"), None, None, None, None, None, None, None, None)
    assert record["name"] == "David Tom" and record["stats"] is None and record["phone"] is None
    assert record["review_breakdown"] is None and record["service_regions"] == []


def test_agent_reviews():
    reviews = P.parse_agent_reviews(payload("agent_all_reviews"))
    assert len(reviews) == 18 and reviews[0]["year"] == 2026 and reviews[-1]["year"] == 2022
    first = reviews[0]
    assert first["stars"] == 4 and first["rating"] == 8 and first["reviewer"] == "Chang Wei Ling"
    assert first["deal"]["side"] == "buyer" and first["deal"]["price"] == 640000 and first["deal"]["closed_date"]
    assert first["property"]["city"] == "Flushing" and first["property"]["link"].endswith("/home/20845995")
    assert P.parse_agent_reviews(None) == []


# ---- housing market ------------------------------------------------------------------

def test_market_overview():
    record = P.parse_market_overview(payload("market_insights_page"), payload("market_trends"),
                                     payload("market_compete-score"), payload("market_graph_home_prices"),
                                     payload("market_graph_demand"), payload("market_affordability"),
                                     payload("market_climate-data"), payload("market_walk-score-data"),
                                     payload("market_nearby-compete-scores"))
    assert record["region"]["id"] == "neighborhood/165450" and record["region"]["name"] == "Allandale"
    assert record["title"] == "Allandale, TX Housing Market"
    assert record["trends"]["median_sale_price"] == 953000 and record["trends"]["median_sale_to_list_percent"] == 99.1
    assert record["trends"]["sale_price_year_over_year_percent"] == 33.7
    assert record["compete_score"]["score"] == 61 and record["compete_score"]["sale_to_list_ratio"] == 0.975
    assert record["nearby_compete_scores"][0]["region"] == "Tarrytown"
    prices = record["price_history"]["median_sale_price"]
    assert prices["latest"] == 871756 and len(prices["series"]) == 60 and prices["series"][0]["date"] == "2026-09-30"
    assert record["demand_history"]["sale_to_list_price"]["series"][0]["value"] == pytest.approx(0.968)
    assert record["affordability"]["as_of"] == "2026-09-01" and record["affordability"]["by_bedrooms"][0]["beds"] == 5
    assert record["climate"]["heat"]["label"] == "Severe" and record["climate"]["flood"]["properties_at_risk_percent"] == 2.57
    assert record["walk_scores"]["walk_score"] == pytest.approx(53.2532)


def test_market_overview_sparse():
    record = P.parse_market_overview(None, None, None, None, None, None, None, None, None)
    assert record["region"]["id"] is None and record["trends"] is None and record["price_history"] is None
    assert fixture("market_insights_page_bad")["resultCode"] == 122


def test_market_recently_sold_and_schools():
    sold = P.parse_recently_sold(payload("market_recently_sold"))
    assert len(sold["homes"]) == 8 and sold["homes"][0]["search_status"] == "sold"
    assert sold["homes"][0]["is_price_hidden"] is True and sold["homes"][0]["price"] == 795000
    assert sold["link"] == "https://www.redfin.com/neighborhood/165450/TX/Austin/Allandale/recently-sold"
    schools = P.parse_best_schools(payload("market_best-schools"))
    assert schools["counts"] == {"elementary": 3, "middle": 2, "high": 1}
    assert schools["elementary"][0]["name"] == "Gullett Elementary School"
    assert P.parse_best_schools(None)["elementary"] == []


# ---- the details fan-out degrades instead of failing ---------------------------------

def test_details_fan_out_survives_a_refused_section(monkeypatch):
    """A section the site refuses after the retries (the per-IP /avm refusal)
    comes back null, is named in unavailable_fields, and the record is not
    cached; aboveTheFold refused still fails the whole call."""
    from redfin import api, fetch

    def fake_payload(path, params=None, *, label=None, token=False, optional=False):
        name = path.split("/stingray/api/", 1)[1]
        for prefix in ("v1/home/details/", "v2/home/details/", "home/details/"):
            if name.startswith(prefix):
                name = name[len(prefix):]
                break
        if name == "avm":
            raise fetch.RedfinBlocked("HTTP 403 on avm")
        return payload("property_austin_" + name.replace("/", "_")) \
            if os.path.exists(os.path.join(FX, "property_austin_" + name.replace("/", "_") + ".json")) else None

    monkeypatch.setattr(api, "get_payload", fake_payload)
    monkeypatch.setattr(api, "get_payload_optional", lambda path, params=None, **kw: fake_payload(path, params, **kw))
    stored = []
    monkeypatch.setattr(api, "cached_call", lambda ns, data, fn, ttl: (lambda r: (stored.append(r), r)[1])(fn(**data)))
    info = {"property_id": 31466150, "listing_id": 224889714, "path": "/TX/Austin/6005-Shoalwood-Ave-78757/home/31466150"}
    result = api._cached_record(info)
    assert isinstance(result, api.DontCache)
    record = result.data
    assert record["unavailable_fields"] == ["redfin_estimate"] and record["redfin_estimate"] is None
    assert record["price"] == 750000 and record["public_records"]["apn"] == "231646"

    def refuse_page(path, params=None, **kw):
        if "aboveTheFold" in path:
            raise fetch.RedfinBlocked("HTTP 403 on aboveTheFold")
        return fake_payload(path, params, **kw)
    monkeypatch.setattr(api, "get_payload", refuse_page)
    with pytest.raises(fetch.RedfinBlocked):
        api._property_record(info)
