from datetime import datetime, timezone
from pathlib import Path

from podcast_alpha.config import Source, load_sources
from podcast_alpha.ingest import Episode
from podcast_alpha.people import apply_source_tiers

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def ep(show, url="", feed=None):
    return Episode(id="x", source_id="person:X", source_name=show, tier=2, title="t", url=url, published_at=NOW, feed_url=feed)


def test_person_hits_inherit_show_tier():
    srcs = [Source(id="tbpn", name="TBPN", tier=1, rss="https://feeds.transistor.fm/technology-brother", itunes_id=1772360235),
            Source(id="t3", name="Ticker Symbol YOU", tier=3, rss="https://x.example/rss")]
    eps = [ep("Other", feed="http://feeds.transistor.fm/technology-brother/"),
           ep("Renamed", url="https://podcasts.apple.com/us/podcast/x/id1772360235?i=1"),
           ep("ticker symbol you!"), ep("Unknown Show")]
    assert [e.tier for e in apply_source_tiers(eps, srcs)] == [1, 1, 3, 2]


def test_tbpn_is_tier_one_in_sources():
    srcs, _ = load_sources(Path(__file__).parents[1] / "sources.yaml")
    assert next(s for s in srcs if s.id == "tbpn").tier == 1
