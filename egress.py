"""The one egress helper the redfin module asks for: the Accept-Language its
requests announce. Redfin is a US site, so this kit always sends US English."""

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def site_accept_language(site):
    return ACCEPT_LANGUAGE
