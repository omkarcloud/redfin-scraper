# 🏡 Redfin Scraper

Redfin Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Redfin data for free.

## ✨ What Can I Get?

- 🏡 **Full details on homes in all 50 states** — price, photos, description, agents, price & tax history, zoning & permits
- 🔍 **For-sale, sold & rental search with 15 filters** — any city, ZIP, neighborhood, map area or Redfin URL, plus 5 years of sold comps
- 💰 **Redfin Estimate with a 5-year value history** — comparable sales, rent estimate, similar listings & market competition
- 🤝 **Agents, reviews & housing markets** — agent emails, phones & deal stats, every review, median prices & 5-year trends

## 🎥 Example: A Full Redfin Home

```json
{
  "property_id": 6835032,
  "mls_id": "26991329",
  "link": "https://www.redfin.com/CA/Beverly-Hills/555-Vick-Pl-90210/home/6835032",
  "status": "Active",
  "property_type": "house",
  "price": 74500000,
  "price_per_sqft": 6773,
  "beds": 7,
  "baths": 10.0,
  "sqft": 11000,
  "lot_sqft": 46601,
  "address": {
    "street": "555 Vick Pl",
    "city": "Beverly Hills",
    "state": "CA",
    "zip": "90210",
    "latitude": 34.1056451,
    "longitude": -118.3989669
  },
  "description": "An exceptionally rare double-lot compound in Trousdale Estates, comprising two side-by-side residences seamlessly connected across two oversized flag lots totaling approximately 50,000 square feet. Privately positioned at the end of a quiet cul-de-sac on one of Trousdale's most coveted streets, the property offers extraordinary scale, privacy, architecture, and unobstructed city-to-ocean views...",
  "listing_agents": [{ "name": "Kurt Rappaport", "license": "01036061" }],
  "photos": { "count": 71, "primary": "https://ssl.cdn-redfin.com/photo/40/bigphoto/329/26991329_5.jpg" },
  "price_history": [{ "date": "2026-09-22", "event": "Listed", "price": 74500000, "source": "TheMLS" }],
  "redfin_estimate": { "value": 62338668.61, "is_available": true, "comparables_count": 6 },
  "rent_estimate": { "value": 10955, "low": 10875, "high": 11128, "currency": "USD" },
  "public_records": { "year_built": 1973, "apn": "4391020009", "tax": { "roll_year": 2025, "taxes_due": 93739.17 } },
  "market_competition": { "region": "Westside LA", "compete_score": 44, "days_on_market": 62.0 },
  "mortgage": { "property_tax_rate_percent": 1.25, "rates": { "thirty_year_fixed": 7.25, "fifteen_year_fixed": 6.5 } }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Redfin Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/redfin-scraper
cd redfin-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

The first call opens Google Chrome for about 2 seconds to earn Redfin's access cookie, so have Chrome installed. Every call after that is plain HTTP.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/properties/details?property=6835032"
```

```json
{
  "property_id": 6835032,
  "link": "https://www.redfin.com/CA/Beverly-Hills/555-Vick-Pl-90210/home/6835032",
  "status": "Active",
  "property_type": "house",
  "price": 74500000,
  "beds": 7,
  "baths": 10.0,
  "sqft": 11000,
  "lot_sqft": 46601,
  "address": {
    "street": "555 Vick Pl",
    "city": "Beverly Hills",
    "state": "CA",
    "zip": "90210",
    "neighborhood": "Westside LA"
  },
  "listing_agents": [{ "name": "Kurt Rappaport", "license": "01036061" }],
  "photos": { "count": 71, "primary": "https://ssl.cdn-redfin.com/photo/40/bigphoto/329/26991329_5.jpg" },
  "price_history": [{ "date": "2026-09-22", "event": "Listed", "price": 74500000, "source": "TheMLS" }],
  "walk_scores": { "walk_score": 20.0, "walk_score_label": "Car-Dependent", "bike_score": 3.0 }
}
```

All 18 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

18 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Property Details | `/properties/details` | Everything about one home: price, photos, history, taxes, agents, estimate |
| Search Locations | `/locations/search` | Cities, ZIPs, neighborhoods, schools and addresses with their Redfin IDs |
| Search For Sale · Sold · Rentals | `/properties/search-sale` · `/properties/search-sold` · `/properties/search-rent` | 50 homes per page with price, beds, size, photos and agent |
| Search By Area | `/properties/search-coordinates` | Homes for sale or sold inside any map polygon or radius |
| Search By URL | `/properties/search-url` | Any Redfin search page, filters included, as clean JSON |
| Property Estimate | `/properties/estimate` | Redfin Estimate, comparable sales and a 5-year value history |
| Property Schools | `/properties/schools` | Assigned schools with GreatSchools ratings, reviews and distance |
| Property Climate Risk | `/properties/climate-risk` | Flood, fire, heat, wind and air risk with insurance estimates |
| Property Permits | `/properties/permits` | Building permits and zoning code for any home |
| Rental Details | `/rentals/details` | Floor plans, unit-by-unit rents, fees, pet policy and amenities |
| Search Agents | `/agents/search` | Every agent in an area with email, phone and deal counts |
| Agent Details | `/agents/details` | Contact, license, rating, deal stats, team and active listings |
| Agent Reviews | `/agents/reviews` | Every review with stars, text, date and the deal behind it |
| Housing Market | `/markets/overview` | Median prices, days on market, compete score and 5-year trends |
| Recently Sold | `/markets/recently-sold` | The latest home sales in any area |
| Best Schools | `/markets/schools` | Top-rated elementary, middle and high schools in any area |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-redfin-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-redfin-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-redfin-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/properties/search-sale",
    params={"location": "Beverly Hills, CA", "min_price": 5000000},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Redfin Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Redfin%20Scraper%20API.)

[![Ask Us by Email about Redfin Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Redfin%20Scraper%20API&body=I%20need%20help%20using%20the%20Redfin%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/redfin-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/redfin-scraper)
