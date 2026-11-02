"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. Params are validated by the same schemas the API uses, so a
place name, a ZIP, a region id or a redfin.com link all work.
"""
import json
import os

from redfin import api, schemas

os.makedirs("output", exist_ok=True)


def run(schema_cls, fn, **params):
    """Validate params like the API does, then call the scraper function."""
    return fn(**schema_cls().load(params))


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a property id, any redfin.com home link or a street address
    save("property_6835032.json", run(schemas.PropertySchema, api.property_details,
                                      property="https://www.redfin.com/CA/Beverly-Hills/555-Vick-Pl-90210/home/6835032"))

    # 50 homes per page, filter by price, beds, size and type
    save("for_sale_beverly_hills.json", run(schemas.SearchSaleSchema, api.properties_search_sale,
                                            location="Beverly Hills, CA", min_price=5000000))

    # median prices, days on market, compete score and 5-year trends
    save("market_beverly_hills.json", run(schemas.MarketSchema, api.market_overview, location="Beverly Hills, CA"))
