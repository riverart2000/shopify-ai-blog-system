"""The curated list of directories worth a listing, ranked by the trust they lend.

A client who searches the business name should find the same name, phone and
story on Google, a review platform, a couple of UK directories and the places
where the workshops and coaching are sold. Entries are curated by hand (an
LLM never adds one unseen). `trust` is how much a listing there reassures a
buyer; `reach` is how many people it brings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KINDS = {
    "search_maps": "Search and maps",
    "reviews": "Reviews and trust",
    "business_directory": "UK business directories",
    "events": "Events and workshops",
    "services": "Coaching marketplaces",
    "social_profile": "Professional profiles",
    "product_comparison": "Product comparison feeds",
    "local": "Local and neighbourhood",
}

NEEDS_LABELS = {
    "account": "an account", "email_verify": "an email code", "phone_verify": "a phone call or text",
    "postcard": "a postcard or video verification", "feed": "a product feed URL", "address": "a public address",
    "apple_id": "an Apple ID", "microsoft": "a Microsoft account", "google": "the bioluxelab Google account",
    "paid": "a paid plan",
}


@dataclass(frozen=True)
class Directory:
    id: str
    name: str
    kind: str
    url: str                      # public home
    submit_url: str               # where the form or sign-up lives (the fill job opens this)
    why: str                      # one sentence for the owner
    cost: str = "free"            # free | freemium | paid | pay_per_lead
    trust: int = 5                # 1-10
    reach: int = 3                # 1-10
    needs: tuple[str, ...] = ()
    fill: str = "relay"           # relay | manual | feed | done
    notes: str = ""
    limits: dict[str, int] = field(default_factory=dict)   # profile field -> max chars on this site
    domains: tuple[str, ...] = ()  # hostnames that count as "found" in a search
    requires_address: bool = False

    @property
    def hosts(self) -> tuple[str, ...]:
        from urllib.parse import urlsplit

        own = (urlsplit(self.url).hostname or "").lower().removeprefix("www.")
        return tuple(dict.fromkeys((own, *self.domains)))


DIRECTORIES: tuple[Directory, ...] = (
    Directory(
        "google_business", "Google Business Profile", "search_maps",
        "https://business.google.com/", "https://business.google.com/",
        "The first thing a client sees when they search the name: hours, photos, reviews, offers.",
        trust=10, reach=10, needs=("google", "postcard"), fill="manual",
        notes="Already set up. Keep it the source of truth: the profile below should match it word for word. "
              "Post offers and workshop dates as Updates, add the services, and ask every customer for a Google review.",
        domains=("google.com", "maps.google.com", "g.page", "business.google.com"),
    ),
    Directory(
        "bing_places", "Bing Places for Business", "search_maps",
        "https://www.bingplaces.com/", "https://www.bingplaces.com/",
        "Bing, Edge, Windows search and Copilot answers all read from it.",
        trust=7, reach=5, needs=("microsoft",), fill="manual",
        notes="Use 'Import from Google Business Profile' and it copies everything in one click; then verify once.",
    ),
    Directory(
        "apple_business_connect", "Apple Business Connect", "search_maps",
        "https://businessconnect.apple.com/", "https://businessconnect.apple.com/",
        "Apple Maps, Siri and Spotlight on every iPhone.",
        trust=7, reach=5, needs=("apple_id", "phone_verify"),
        notes="Service-area businesses can hide the street address. Add the logo and the three best photos.",
        limits={"description": 500},
    ),
    Directory(
        "trustpilot", "Trustpilot", "reviews",
        "https://uk.trustpilot.com/", "https://business.trustpilot.com/signup",
        "The review site UK shoppers check before a first order; a claimed profile with replies beats an empty one.",
        trust=10, reach=6, needs=("account", "email_verify"),
        notes="Free plan. Invite every customer after delivery (Shopify Messaging can send the link) and reply to every review. "
              "Imported reviews cannot be shown here, which is the point: these are real.",
        limits={"description": 1000}, domains=("trustpilot.com", "uk.trustpilot.com"),
    ),
    Directory(
        "yelp", "Yelp UK", "reviews",
        "https://www.yelp.co.uk/", "https://biz.yelp.com/signup",
        "Still indexed high for '<name> reviews' searches in the UK.",
        trust=6, reach=3, needs=("account", "phone_verify"), requires_address=True,
        notes="Needs a business address; skip until there is a public one.", domains=("yelp.co.uk", "yelp.com"),
    ),
    Directory(
        "yell", "Yell.com", "business_directory",
        "https://www.yell.com/", "https://business.yell.com/free-listing/",
        "The best-known UK directory; a free listing is a trusted citation.",
        trust=6, reach=4, needs=("account", "phone_verify"),
        limits={"description": 600},
    ),
    Directory(
        "freeindex", "FreeIndex", "business_directory",
        "https://www.freeindex.co.uk/", "https://www.freeindex.co.uk/add-business.htm",
        "Free UK directory with its own reviews; ranks for '<name> London' searches.",
        trust=5, reach=3, needs=("account", "email_verify"),
        limits={"description": 1000},
    ),
    Directory(
        "cylex", "Cylex UK", "business_directory",
        "https://www.cylex-uk.co.uk/", "https://www.cylex-uk.co.uk/add-company.html",
        "Large free directory; another consistent citation of the name and phone.",
        trust=4, reach=2, needs=("email_verify",), limits={"description": 1000},
    ),
    Directory(
        "hotfrog", "Hotfrog UK", "business_directory",
        "https://www.hotfrog.co.uk/", "https://www.hotfrog.co.uk/AddYourBusiness",
        "Free directory with a description field long enough for the full story.",
        trust=4, reach=2, needs=("email_verify",), limits={"description": 2000},
    ),
    Directory(
        "scoot", "Scoot", "business_directory",
        "https://www.scoot.co.uk/", "https://www.scoot.co.uk/add-business",
        "Feeds several UK directories (Touch Local, The Independent's listings).",
        trust=4, reach=2, needs=("email_verify",), limits={"description": 500},
    ),
    Directory(
        "thomson_local", "Thomson Local", "business_directory",
        "https://www.thomsonlocal.com/", "https://www.thomsonlocal.com/add-business",
        "Old-name UK directory that still appears in branded searches.",
        trust=4, reach=2, needs=("email_verify",), limits={"description": 500},
    ),
    Directory(
        "uk_small_business", "UK Small Business Directory", "business_directory",
        "https://www.uksmallbusinessdirectory.co.uk/", "https://www.uksmallbusinessdirectory.co.uk/add-listing/",
        "Free, quick, and it links back to the store.",
        trust=3, reach=1, needs=("email_verify",), limits={"description": 500},
    ),
    Directory(
        "eventbrite", "Eventbrite", "events",
        "https://www.eventbrite.co.uk/", "https://www.eventbrite.co.uk/organizations/events/create",
        "Where Londoners look for workshops; the organiser page doubles as a public profile with past events and followers.",
        trust=6, reach=8, needs=("account",),
        notes="Free for free events; paid tickets carry a fee. List the online workshop and the cohort with the real dates, "
              "link the Shopify product for payment or sell the ticket here.",
        limits={"description": 2500},
    ),
    Directory(
        "superprof", "Superprof", "services",
        "https://www.superprof.co.uk/", "https://www.superprof.co.uk/become-tutor.html",
        "Largest UK tutoring marketplace; wellness and lifestyle coaching are categories.",
        trust=5, reach=6, needs=("account", "email_verify"),
        notes="Mirror the Apprentus profile. Reviews collected here show on Google.", limits={"description": 2000},
    ),
    Directory(
        "apprentus", "Apprentus", "services",
        "https://www.apprentus.com/", "https://www.apprentus.com/en-us/in/joebains",
        "The coaching profile that already exists; keep it in step with the store and link it from the Meet Joe page.",
        trust=5, reach=4, fill="done",
        notes="Live. Add the kits and the workshop to the profile text, and point the website field at the store.",
        domains=("apprentus.co.uk",),
    ),
    Directory(
        "bark", "Bark", "services",
        "https://www.bark.com/", "https://www.bark.com/en/gb/sellers/",
        "Leads for coaching and corporate wellness workshops in London.",
        cost="pay_per_lead", trust=4, reach=5, needs=("account", "paid"),
        notes="Free profile; you pay per lead you choose to answer. Try it after the first organic bookings.",
    ),
    Directory(
        "linkedin_company", "LinkedIn company page", "social_profile",
        "https://www.linkedin.com/", "https://www.linkedin.com/company/setup/new/",
        "HR and founders check LinkedIn before booking a workshop; the page already ranks for the name.",
        trust=8, reach=5, needs=("account",),
        notes="A page exists (posts rank for the brand). Fill the About, add the services and link the founder profile.",
        limits={"tagline": 120, "description": 2000}, domains=("linkedin.com", "uk.linkedin.com"),
    ),
    Directory(
        "nextdoor", "Nextdoor Business", "local",
        "https://business.nextdoor.co.uk/", "https://business.nextdoor.co.uk/",
        "Neighbourhood recommendations in London; good for the in-person coaching.",
        trust=5, reach=3, needs=("account", "address"), requires_address=True,
        notes="Needs a public address. Skip until there is one.",
        domains=("nextdoor.co.uk", "nextdoor.com"),
    ),
    Directory(
        "pricespy", "PriceSpy", "product_comparison",
        "https://pricespy.co.uk/", "https://pricespy.co.uk/info/shop",
        "Free price comparison listing from a product feed; buyers compare before ordering.",
        trust=4, reach=4, needs=("feed", "account"), fill="feed",
        notes="Send the Google Merchant feed URL. Products must show the same price as the store.",
    ),
    Directory(
        "companies_house", "Companies House", "reviews",
        "https://find-and-update.company-information.service.gov.uk/",
        "https://find-and-update.company-information.service.gov.uk/",
        "If the business is a limited company, this is the record a careful client checks first.",
        trust=7, reach=1, fill="manual",
        notes="Nothing to submit. If trading as a sole trader, say so plainly on the Meet Joe page instead.",
        domains=("company-information.service.gov.uk", "companieshouse.gov.uk"),
    ),
)

BY_ID: dict[str, Directory] = {d.id: d for d in DIRECTORIES}


def get_directory(directory_id: str) -> Directory | None:
    return BY_ID.get(str(directory_id or "").strip())


def ranked() -> list[Directory]:
    """Highest trust first, then reach, then name."""
    return sorted(DIRECTORIES, key=lambda d: (-d.trust, -d.reach, d.name.lower()))


def needs_text(directory: Directory) -> str:
    parts = [NEEDS_LABELS.get(n, n) for n in directory.needs]
    return ", ".join(parts)
