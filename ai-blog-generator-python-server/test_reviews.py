from __future__ import annotations

import base64
import io

import pytest
import pytest_asyncio
from PIL import Image

import db
from services import review_service


@pytest_asyncio.fixture
async def review_db(tmp_path):
    previous = db.get_db_path()
    path = str(tmp_path / "reviews.db")
    db.set_db_path(path)
    await db.init_db()
    await db.upsert_store({
        "id": "review-store", "name": "BioLuxeLab",
        "myshopify_domain": "review-store.myshopify.com", "custom_domain": "bioluxelab.com",
        "client_id": "client", "client_secret": "secret", "default_blog_handle": "news",
        "default_author": "Store Team", "sort_order": 0, "password_hash": "",
    })
    yield path
    db.set_db_path(previous)


def review_data(**overrides):
    data = {
        "review_type": "product", "product_handle": "demo-product",
        "product_title": "Demo Product", "rating": 5, "review_title": "Excellent",
        "review_body": "This was genuinely useful and exactly as described.",
        "reviewer_name": "Alex Smith", "reviewer_email": "alex@example.com",
        "ip_hash": "hashed-ip",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_reviews_are_private_until_approved_and_aggregate_correctly(review_db):
    first = await db.create_review("review-store", review_data())
    second = await db.create_review("review-store", review_data(
        reviewer_email="sam@example.com", rating=1, review_title="Not for me",
        review_body="The product did not suit my particular needs.", ip_hash="other-ip",
    ))

    public, total = await db.list_reviews(
        "review-store", public=True, review_type="product", product_handle="demo-product"
    )
    assert public == []
    assert total == 0

    await db.moderate_review("review-store", first["id"], status="published")
    await db.moderate_review("review-store", second["id"], status="published")
    summary = await db.get_review_summary("review-store", "demo-product", "product")
    assert summary == {
        "count": 2, "average": 3.0,
        "distribution": {"1": 1, "2": 0, "3": 0, "4": 0, "5": 1},
        "external_count": 0, "facebook_count": 0, "total_count": 2,
    }

    await db.moderate_review("review-store", second["id"], status="hidden")
    summary = await db.get_review_summary("review-store", "demo-product", "product")
    assert summary["count"] == 1
    assert summary["average"] == 5.0


@pytest.mark.asyncio
async def test_rate_limit_duplicate_detection_and_csv_formula_protection(review_db):
    await db.create_review("review-store", review_data(review_title="=HYPERLINK bad"))
    assert await db.rate_limit_count("review-store", "hashed-ip", 0) == 1
    assert await db.duplicate_count(
        "review-store", "ALEX@example.com", "product", "demo-product", 0
    ) == 1
    csv_text = await db.export_reviews_csv("review-store")
    assert "'=HYPERLINK bad" in csv_text
    assert "alex@example.com" in csv_text


def test_review_photo_is_validated_stripped_and_resized():
    image = Image.new("RGB", (1800, 900), color=(20, 90, 60))
    raw = io.BytesIO()
    image.save(raw, format="PNG")
    value = "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode("ascii")
    normalized = review_service.normalize_photo(value)
    assert normalized.startswith("data:image/webp;base64,")
    result = Image.open(io.BytesIO(base64.b64decode(normalized.split(",", 1)[1])))
    assert result.width <= 1600
    assert result.height <= 1600


def test_review_photo_rejects_non_image_and_email_validation():
    with pytest.raises(review_service.ReviewValidationError, match="JPEG, PNG or WebP"):
        review_service.normalize_photo("data:text/plain;base64,SGVsbG8=")
    with pytest.raises(review_service.ReviewValidationError, match="valid email"):
        review_service.validate_email("not-an-email")
    assert review_service.validate_email(" Customer@Example.com ") == "customer@example.com"
    assert review_service.validate_facebook_review_url(
        "https://www.facebook.com/bioluxelab/reviews"
    ) == "https://www.facebook.com/bioluxelab/reviews"
    with pytest.raises(review_service.ReviewValidationError, match="facebook.com"):
        review_service.validate_facebook_review_url("https://example.com/fake-review")


def test_moderation_flags_links_capitals_and_suspicious_names():
    flags = review_service.moderation_flags(
        "AMAZING AMAZING AMAZING AMAZING", "VISIT HTTPS://SPAM.EXAMPLE NOW!!!!!!!!", "www.spam.example"
    )
    assert "contains_link" in flags
    assert "mostly_capitals" in flags
    assert "repeated_characters" in flags
    assert "suspicious_name" in flags


@pytest.mark.asyncio
async def test_facebook_reviews_are_visible_but_excluded_from_site_star_average(review_db):
    site = await db.create_review("review-store", review_data(
        review_type="store", product_handle="", product_title="",
    ))
    facebook = await db.create_review("review-store", review_data(
        review_type="store", product_handle="", product_title="",
        reviewer_name="Facebook Customer", reviewer_email="", rating=1,
        review_title="Facebook recommendation",
        review_body="I do not recommend this store based on my experience.",
        source="facebook",
        source_path="https://www.facebook.com/bioluxelab/reviews",
    ))
    await db.moderate_review("review-store", site["id"], status="published")
    await db.moderate_review("review-store", facebook["id"], status="published")

    summary = await db.get_review_summary("review-store", review_type="store")
    assert summary["count"] == 1
    assert summary["average"] == 5.0
    assert summary["facebook_count"] == 1
    assert summary["external_count"] == 1
    assert summary["total_count"] == 2
    assert await db.external_review_duplicate_count(
        "review-store", "facebook", "Facebook Customer",
        "I do not recommend this store based on my experience.",
    ) == 1
