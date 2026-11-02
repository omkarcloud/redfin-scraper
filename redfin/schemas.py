"""Marshmallow request schemas for every /redfin/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair; the resolvers live in redfin/refs.py):

    location   "city/30818", a redfin.com region link, or free text / ZIP
    property   a propertyId, a redfin.com home link, or a street address
    rental     a propertyId or the rental building's redfin.com home link
    agent      an agent slug or the profile / reviews link
"""
from marshmallow import ValidationError, validates_schema

from schema_fields import (
    BaseSchema, ChoiceField, CommaListField, NonNegativeNumber, PageField, PositiveInt, QueryField,
    RefField, StrippedString,
)
from redfin import refs

PAGE_SIZE = 50           # homes per search page (the site's own default is 350; thousands are honoured)
MAX_PAGE = 100
RENTAL_PAGE_SIZE = 50
MAX_POLYGON_POINTS = 50


class LocationRefField(RefField):
    resolver = staticmethod(refs.resolve_location)


class PropertyRefField(RefField):
    resolver = staticmethod(refs.resolve_property)


class RentalRefField(RefField):
    resolver = staticmethod(refs.resolve_property)


class AgentRefField(RefField):
    resolver = staticmethod(refs.resolve_agent)


_LOCATION_DESCRIPTION = ("a region id (city/30818, zipcode/78704, neighborhood/165450, county/2866, "
                         "school/135664, school-district/5816), a redfin.com region link, or a place name / "
                         "ZIP code / address to look up")


class LocationsSearchSchema(BaseSchema):
    query = QueryField(metadata={"description": "place name, ZIP code, neighborhood, school, "
                                                "street address or agent area to look up"})


class _SearchFilters(BaseSchema):
    min_price = NonNegativeNumber(metadata={"description": "minimum list price in USD"})
    max_price = NonNegativeNumber(metadata={"description": "maximum list price in USD"})
    min_beds = PositiveInt(max_value=20, metadata={"description": "minimum bedrooms"})
    max_beds = PositiveInt(max_value=20, metadata={"description": "maximum bedrooms"})
    min_baths = NonNegativeNumber(metadata={"description": "minimum bathrooms (1, 1.5, 2, ...)"})
    min_sqft = PositiveInt(metadata={"description": "minimum interior square feet"})
    max_sqft = PositiveInt(metadata={"description": "maximum interior square feet"})
    min_lot_sqft = PositiveInt(metadata={"description": "minimum lot size in square feet (1 acre = 43560)"})
    max_lot_sqft = PositiveInt(metadata={"description": "maximum lot size in square feet"})
    min_year_built = PositiveInt(max_value=2100, metadata={"description": "earliest year built"})
    max_year_built = PositiveInt(max_value=2100, metadata={"description": "latest year built"})
    max_hoa = NonNegativeNumber(metadata={"description": "maximum HOA dues per month in USD (0 = no HOA)"})
    min_stories = PositiveInt(max_value=50, metadata={"description": "minimum stories"})
    max_stories = PositiveInt(max_value=50, metadata={"description": "maximum stories"})
    property_types = CommaListField(allowed=list(refs.PROPERTY_TYPES), upper=False, metadata={
        "description": "comma list of house, condo, townhouse, multi_family, land, other, manufactured, co_op "
                       "(default: all)"})
    page = PageField(max_page=MAX_PAGE, metadata={"description": f"page of {PAGE_SIZE} homes"})

    @validates_schema
    def _ranges(self, data, **kwargs):
        for low, high in (("min_price", "max_price"), ("min_beds", "max_beds"), ("min_sqft", "max_sqft"),
                          ("min_lot_sqft", "max_lot_sqft"), ("min_year_built", "max_year_built"),
                          ("min_stories", "max_stories")):
            if data.get(low) is not None and data.get(high) is not None and data[low] > data[high]:
                raise ValidationError(f"{low} must not exceed {high}.", high)


class SearchSaleSchema(_SearchFilters):
    location = LocationRefField(metadata={"description": _LOCATION_DESCRIPTION})
    status = ChoiceField(list(refs.SALE_STATUS), load_default="active_and_coming_soon", metadata={
        "description": "active_and_coming_soon (default), active, coming_soon, pending, active_and_pending"})
    sort = ChoiceField(["recommended", "newest", "price_low", "price_high"], load_default="recommended",
                       metadata={"description": "recommended (default), newest, price_low, price_high"})


class SearchSoldSchema(_SearchFilters):
    location = LocationRefField(metadata={"description": _LOCATION_DESCRIPTION})
    sold_within = ChoiceField({str(d): d for d in refs.SOLD_WITHIN_DAYS}, load_default=90, metadata={
        "description": "days back: 7, 30, 90 (default), 180, 365, 730, 1095 or 1825"})
    sort = ChoiceField(["sold_date", "recommended", "price_low", "price_high"], load_default="sold_date",
                       metadata={"description": "sold_date (default, newest sale first), recommended, "
                                                "price_low, price_high"})


class SearchRentSchema(BaseSchema):
    location = LocationRefField(metadata={"description": _LOCATION_DESCRIPTION})
    min_rent = NonNegativeNumber(metadata={"description": "minimum monthly rent in USD"})
    max_rent = NonNegativeNumber(metadata={"description": "maximum monthly rent in USD"})
    min_beds = PositiveInt(max_value=20, metadata={"description": "minimum bedrooms (studios count as 0 and are kept)"})
    max_beds = PositiveInt(max_value=20, metadata={"description": "maximum bedrooms"})
    min_baths = NonNegativeNumber(metadata={"description": "minimum bathrooms"})
    property_types = CommaListField(allowed=list(refs.RENTAL_PROPERTY_TYPES), upper=False, metadata={
        "description": "comma list of apartment, house, condo, townhouse (default: all)"})
    sort = ChoiceField(list(refs.RENT_SORTS), load_default="recommended",
                       metadata={"description": "recommended (default), newest, price_low, price_high"})
    page = PageField(max_page=MAX_PAGE, metadata={"description": f"page of {RENTAL_PAGE_SIZE} rentals"})

    @validates_schema
    def _ranges(self, data, **kwargs):
        if data.get("min_rent") is not None and data.get("max_rent") is not None \
                and data["min_rent"] > data["max_rent"]:
            raise ValidationError("min_rent must not exceed max_rent.", "max_rent")
        if data.get("min_beds") is not None and data.get("max_beds") is not None \
                and data["min_beds"] > data["max_beds"]:
            raise ValidationError("min_beds must not exceed max_beds.", "max_beds")


class PolygonField(StrippedString):
    """'lng lat, lng lat, ...' (3 to 50 points; closed automatically) OR
    'lat,lng,radius_miles' for a circle -> the gis `poly` string."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs_polygon(value)
        except ValueError as e:
            raise ValidationError(str(e))


def refs_polygon(value):
    import math
    pairs = [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    # circle form: lat,lng,radius
    if len(pairs) == 3 and all(" " not in p for p in pairs):
        try:
            lat, lng, radius = (float(p) for p in pairs)
        except ValueError:
            raise ValueError("A circle is 'lat,lng,radius_miles' (e.g. 30.27,-97.74,3).")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180) or not (0.1 <= radius <= 50):
            raise ValueError("A circle needs -90..90 lat, -180..180 lng and a radius of 0.1 to 50 miles.")
        points = []
        for i in range(24):
            angle = 2 * math.pi * i / 24
            d_lat = (radius / 69.0) * math.cos(angle)
            d_lng = (radius / (69.0 * max(math.cos(math.radians(lat)), 0.01))) * math.sin(angle)
            points.append((lng + d_lng, lat + d_lat))
    else:
        points = []
        for pair in pairs:
            parts = pair.split()
            if len(parts) != 2:
                raise ValueError("A polygon is 'lng lat, lng lat, ...' (at least 3 points), "
                                 "or a circle 'lat,lng,radius_miles'.")
            try:
                lng, lat = float(parts[0]), float(parts[1])
            except ValueError:
                raise ValueError("Polygon points must be numbers: 'lng lat, lng lat, ...'.")
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                raise ValueError("Polygon points must be 'lng lat' with lng in -180..180 and lat in -90..90.")
            points.append((lng, lat))
        if len(points) < 3:
            raise ValueError("A polygon needs at least 3 points ('lng lat, lng lat, lng lat').")
        if len(points) > MAX_POLYGON_POINTS:
            raise ValueError(f"At most {MAX_POLYGON_POINTS} polygon points.")
    if points[0] != points[-1]:
        points.append(points[0])
    return ",".join(f"{lng:.6f} {lat:.6f}" for lng, lat in points)


class SearchCoordinatesSchema(_SearchFilters):
    area = PolygonField(required=True, metadata={
        "description": "the area to search: a polygon 'lng lat, lng lat, ...' (3 to 50 points) or a circle "
                       "'lat,lng,radius_miles' (radius up to 50)"})
    listing = ChoiceField(["for_sale", "sold"], load_default="for_sale",
                          metadata={"description": "for_sale (default) or sold (last 90 days)"})
    sort = ChoiceField(["recommended", "newest", "price_low", "price_high", "sold_date"], load_default="recommended",
                       metadata={"description": "recommended (default), newest, price_low, price_high, sold_date"})


class SearchUrlSchema(BaseSchema):
    url = StrippedString(required=True, metadata={
        "description": "any redfin.com search page: a city / ZIP / neighborhood / county / school page, "
                       "with or without its /filter/... tail, for sale, sold or apartments-for-rent"})
    page = PageField(max_page=MAX_PAGE, metadata={"description": f"page of {PAGE_SIZE} homes"})

    @validates_schema
    def _is_region_link(self, data, **kwargs):
        value = data.get("url") or ""
        if not value.startswith(("http://", "https://", "//", "www.redfin.com", "redfin.com")):
            raise ValidationError("Must be a redfin.com link.", "url")
        try:
            refs.resolve_location(value)
        except ValueError as e:
            raise ValidationError(str(e), "url")


class PropertySchema(BaseSchema):
    property = PropertyRefField(metadata={
        "description": "Redfin propertyId (31466150), the home's redfin.com link, or a full street address"})


class RentalSchema(BaseSchema):
    rental = RentalRefField(metadata={
        "description": "the rental building's Redfin propertyId or its redfin.com link"})


class AgentsSearchSchema(BaseSchema):
    location = LocationRefField(metadata={
        "description": "a city or neighborhood: its region id (city/35948), its redfin.com link, "
                       "or a place name to look up"})
    page = PageField(max_page=20, metadata={"description": "page of 50 agents"})


class AgentSchema(BaseSchema):
    agent = AgentRefField(metadata={"description": "agent slug (david-tom) or the redfin.com profile link"})


class MarketSchema(BaseSchema):
    location = LocationRefField(metadata={"description": _LOCATION_DESCRIPTION})
