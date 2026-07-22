"""Idempotent RSS 2.0 output for landing-page social posts."""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


MEDIA_NS = "http://search.yahoo.com/mrss/"
LANDING_NS = "https://revenuemindproai.com/rss/landing-pages"

ET.register_namespace("media", MEDIA_NS)
ET.register_namespace("landing", LANDING_NS)


def _guid_prefix(handle: str) -> str:
    return f"landing-page:{handle}:"


def _new_feed() -> tuple[ET.ElementTree, ET.Element]:
    root = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "BioLuxeLab Landing Page Social Feed"
    ET.SubElement(channel, "link").text = "https://bioluxelab.com/"
    ET.SubElement(channel, "description").text = (
        "Generated social posts for BioLuxeLab product landing pages."
    )
    ET.SubElement(channel, "generator").text = "Shopify AI Blog System"
    return ET.ElementTree(root), channel


def _load_feed(feed_path: Path) -> tuple[ET.ElementTree, ET.Element]:
    if not feed_path.exists():
        return _new_feed()

    tree = ET.parse(feed_path)
    root = tree.getroot()
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError(f"Unsupported RSS document in {feed_path}")
    return tree, channel


def _set_channel_value(channel: ET.Element, name: str, value: str) -> None:
    element = channel.find(name)
    if element is None:
        element = ET.SubElement(channel, name)
    element.text = value


def _description_html(
    caption: str, landing_page_url: str, media_url: str, media_type: str = "image/jpeg"
) -> str:
    caption_html = "<br>".join(escape(caption.strip()).splitlines())
    parts = []
    if media_url and media_type.startswith("video/"):
        parts.append(
            f'<p><video controls playsinline preload="metadata" '
            f'src="{escape(media_url, quote=True)}"></video></p>'
        )
    elif media_url:
        parts.append(
            f'<p><a href="{escape(landing_page_url, quote=True)}">'
            f'<img src="{escape(media_url, quote=True)}" alt="Marketing image"></a></p>'
        )
    if caption_html:
        parts.append(f"<p>{caption_html}</p>")
    parts.append(
        f'<p><a href="{escape(landing_page_url, quote=True)}">View landing page</a></p>'
    )
    return "".join(parts)


def write_product_section(
    feed_path: Path,
    *,
    handle: str,
    product_title: str,
    landing_page_url: str,
    concepts: Iterable[dict],
    videos: Iterable[dict] = (),
) -> dict:
    """Replace one product's RSS items and return its verifiable section."""
    tree, channel = _load_feed(feed_path)
    prefix = _guid_prefix(handle)
    replaced_count = 0

    for item in list(channel.findall("item")):
        guid = (item.findtext("guid") or "").strip()
        item_handle = (item.findtext(f"{{{LANDING_NS}}}productHandle") or "").strip()
        if guid.startswith(prefix) or item_handle == handle:
            channel.remove(item)
            replaced_count += 1

    now = datetime.now(timezone.utc)
    pub_date = format_datetime(now, usegmt=True)
    entries = []

    for concept_data in concepts:
        concept = concept_data.get("concept") or {}
        concept_name = str(concept.get("concept") or concept_data.get("slug") or "Social post")
        concept_slug = str(concept_data.get("slug") or "social-post")
        caption = str(concept.get("social_text") or "").strip()
        image_url = str(concept_data.get("cdn_url") or "").strip()
        guid = f"{prefix}{concept_slug}"
        title = f"{product_title} — {concept_name}" if product_title else concept_name

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = landing_page_url
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid
        ET.SubElement(item, "pubDate").text = pub_date
        ET.SubElement(item, "description").text = _description_html(
            caption, landing_page_url, image_url
        )
        ET.SubElement(item, f"{{{LANDING_NS}}}productHandle").text = handle
        ET.SubElement(item, f"{{{LANDING_NS}}}concept").text = concept_name
        if image_url:
            ET.SubElement(
                item,
                "enclosure",
                {"url": image_url, "length": "0", "type": "image/jpeg"},
            )
            ET.SubElement(
                item,
                f"{{{MEDIA_NS}}}content",
                {"url": image_url, "medium": "image", "type": "image/jpeg"},
            )

        entries.append(
            {
                "guid": guid,
                "title": title,
                "concept": concept_name,
                "link": landing_page_url,
                "image_url": image_url,
                "description": caption,
                "media_type": "image/jpeg",
            }
        )

    for video_data in videos:
        concept_name = str(video_data.get("concept") or video_data.get("slug") or "Marketing video")
        concept_slug = str(video_data.get("slug") or "marketing-video")
        caption = str(video_data.get("posting_text") or "").strip()
        video_url = str(video_data.get("cdn_url") or "").strip()
        if not video_url:
            continue
        guid = f"{prefix}video:{concept_slug}"
        title = f"{product_title} — {concept_name} video" if product_title else f"{concept_name} video"

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = landing_page_url
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = guid
        ET.SubElement(item, "pubDate").text = pub_date
        ET.SubElement(item, "description").text = _description_html(
            caption, landing_page_url, video_url, "video/mp4"
        )
        ET.SubElement(item, f"{{{LANDING_NS}}}productHandle").text = handle
        ET.SubElement(item, f"{{{LANDING_NS}}}concept").text = concept_name
        ET.SubElement(item, f"{{{LANDING_NS}}}mediaKind").text = "video"
        ET.SubElement(
            item,
            "enclosure",
            {"url": video_url, "length": "0", "type": "video/mp4"},
        )
        ET.SubElement(
            item,
            f"{{{MEDIA_NS}}}content",
            {"url": video_url, "medium": "video", "type": "video/mp4"},
        )
        entries.append(
            {
                "guid": guid,
                "title": title,
                "concept": concept_name,
                "link": landing_page_url,
                "image_url": "",
                "video_url": video_url,
                "media_type": "video/mp4",
                "description": caption,
            }
        )

    _set_channel_value(channel, "lastBuildDate", pub_date)
    feed_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = feed_path.with_suffix(f"{feed_path.suffix}.tmp")
    tree.write(temporary_path, encoding="utf-8", xml_declaration=True)
    temporary_path.replace(feed_path)

    return {
        "action": "updated" if replaced_count else "created",
        "duplicate_prevented": replaced_count > 0,
        "replaced_count": replaced_count,
        "entry_count": len(entries),
        "product_handle": handle,
        "entries": entries,
    }


def read_product_section(feed_path: Path, handle: str) -> dict:
    """Return the existing RSS items belonging to one product."""
    if not feed_path.exists():
        return {
            "entry_count": 0,
            "product_handle": handle,
            "entries": [],
        }

    _tree, channel = _load_feed(feed_path)
    prefix = _guid_prefix(handle)
    entries = []
    for item in channel.findall("item"):
        guid = (item.findtext("guid") or "").strip()
        item_handle = (item.findtext(f"{{{LANDING_NS}}}productHandle") or "").strip()
        if not guid.startswith(prefix) and item_handle != handle:
            continue
        enclosure = item.find("enclosure")
        media_type = enclosure.get("type", "") if enclosure is not None else ""
        media_url = enclosure.get("url", "") if enclosure is not None else ""
        entries.append(
            {
                "guid": guid,
                "title": (item.findtext("title") or "").strip(),
                "concept": (item.findtext(f"{{{LANDING_NS}}}concept") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "image_url": media_url if media_type.startswith("image/") else "",
                "video_url": media_url if media_type.startswith("video/") else "",
                "media_type": media_type,
                "description": (item.findtext("description") or "").strip(),
            }
        )

    return {
        "entry_count": len(entries),
        "product_handle": handle,
        "entries": entries,
    }
