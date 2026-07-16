"""Daemon to generate an RSS feed from social marketing assets for Publer ingestion.
Drip-feeds one new item every N hours.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from xml.sax.saxutils import escape

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("rss_daemon")


def parse_args():
    parser = argparse.ArgumentParser(description="RSS Drip-feed Daemon")
    parser.add_argument("--social-dir", type=Path, default=Path("social"))
    parser.add_argument("--output", type=Path, default=Path("social/feed.xml"))
    parser.add_argument("--state-file", type=Path, default=Path("rss_state.json"))
    parser.add_argument("--base-url", type=str, default="https://YOUR_DOMAIN.com/social/")
    parser.add_argument("--feed-title", type=str, default="Product Marketing Social Feed")
    parser.add_argument("--feed-desc", type=str, default="Automated marketing posts ready for publishing")
    parser.add_argument("--drip-interval-hours", type=float, default=2.0, help="Hours between publishing new items to the feed")
    parser.add_argument("--scan-interval-minutes", type=float, default=15.0, help="Minutes between scanning for new items")
    parser.add_argument("--run-once", action="store_true", help="Run one cycle and exit")
    return parser.parse_args()


class RSSDaemon:
    def __init__(self, args):
        self.args = args
        self.state_path = args.state_file
        self.state = self.load_state()

    def load_state(self) -> dict:
        if self.state_path.exists():
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.error("Failed to load state: %s", e)
        return {"exposed_items": {}, "last_drip_time": None}

    def save_state(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def scan_social_dir(self) -> List[str]:
        """Return a list of available stems (where both .txt and .jpg exist)."""
        available = []
        if not self.args.social_dir.exists():
            return available
        for txt_path in sorted(self.args.social_dir.glob("*.txt")):
            img_path = self.args.social_dir / f"{txt_path.stem}.jpg"
            if img_path.exists():
                available.append(txt_path.stem)
        return available

    def build_rss_feed(self) -> str:
        base_url = self.args.base_url
        if not base_url.endswith("/"):
            base_url += "/"

        now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        items_xml = []

        # Sort exposed items by publish date descending (newest first)
        exposed = sorted(self.state["exposed_items"].items(), key=lambda x: x[1], reverse=True)

        for stem, pub_date_iso in exposed:
            txt_path = self.args.social_dir / f"{stem}.txt"
            img_path = self.args.social_dir / f"{stem}.jpg"
            
            if not txt_path.exists() or not img_path.exists():
                continue

            content = txt_path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            lines = content.splitlines()
            title = lines[0].strip()
            body = content
            img_url = f"{base_url}{img_path.name}"
            item_link = img_url

            try:
                pub_dt = datetime.fromisoformat(pub_date_iso.replace("Z", "+00:00"))
                pub_date_str = pub_dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
            except Exception:
                pub_date_str = now_str

            item_xml = f"""        <item>
            <title>{escape(title)}</title>
            <link>{escape(item_link)}</link>
            <description>{escape(body)}</description>
            <enclosure url="{escape(img_url)}" type="image/jpeg" length="{img_path.stat().st_size}" />
            <guid isPermaLink="false">{escape(stem)}</guid>
            <pubDate>{pub_date_str}</pubDate>
        </item>"""
            items_xml.append(item_xml)

        items_str = "\n".join(items_xml)
        
        return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
    <channel>
        <title>{escape(self.args.feed_title)}</title>
        <link>{escape(base_url)}</link>
        <description>{escape(self.args.feed_desc)}</description>
        <lastBuildDate>{now_str}</lastBuildDate>
        <pubDate>{now_str}</pubDate>
{items_str}
    </channel>
</rss>"""

    def tick(self):
        available = self.scan_social_dir()
        now = datetime.now(timezone.utc)
        
        # Check if we need to drip
        last_drip_iso = self.state.get("last_drip_time")
        should_drip = False
        
        if not last_drip_iso:
            should_drip = True
        else:
            try:
                last_drip = datetime.fromisoformat(last_drip_iso.replace("Z", "+00:00"))
                if (now - last_drip).total_seconds() >= (self.args.drip_interval_hours * 3600):
                    should_drip = True
            except Exception:
                should_drip = True

        unexposed = [stem for stem in available if stem not in self.state["exposed_items"]]
        
        if should_drip and unexposed:
            # Add the oldest unexposed (or just the first in sorted order)
            new_item = unexposed[0]
            self.state["exposed_items"][new_item] = now.isoformat()
            self.state["last_drip_time"] = now.isoformat()
            self.save_state()
            log.info("Dripped new item: %s (%d remaining)", new_item, len(unexposed) - 1)
            
            # Rebuild feed
            rss_xml = self.build_rss_feed()
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            self.args.output.write_text(rss_xml, encoding="utf-8")
            log.info("Updated feed.xml at %s", self.args.output)
            
        elif not unexposed:
            pass # Keep it quiet when idle
        else:
            log.info("Waiting for next drip. %d items queued.", len(unexposed))
            
        # Ensure feed exists on disk even if we didn't drip anything yet
        if not self.args.output.exists() or (not unexposed and len(self.state["exposed_items"]) == 0):
            rss_xml = self.build_rss_feed()
            self.args.output.parent.mkdir(parents=True, exist_ok=True)
            self.args.output.write_text(rss_xml, encoding="utf-8")

    def run(self):
        log.info("Starting RSS Drip-feed Daemon...")
        log.info("Base URL: %s", self.args.base_url)
        log.info("Drip interval: %.1f hours", self.args.drip_interval_hours)
        log.info("Scan interval: %.1f minutes", self.args.scan_interval_minutes)
        
        if self.args.run_once:
            self.tick()
            return

        while True:
            try:
                self.tick()
            except Exception as e:
                log.error("Error in tick: %s", e, exc_info=True)
            
            time.sleep(self.args.scan_interval_minutes * 60)


if __name__ == "__main__":
    daemon = RSSDaemon(parse_args())
    daemon.run()
