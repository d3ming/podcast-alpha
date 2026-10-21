from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import pytest

from podcast_alpha import holdings
from podcast_alpha.config import load_sources
from podcast_alpha.ingest import Episode, canonical_id, from_rss, parse_date
from podcast_alpha.store import Store
from podcast_alpha.transcribe import fmt_ts, parse_cues, parse_json, title_match, to_seconds

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
RSS = """<?xml version="1.0"?><rss xmlns:podcast="https://podcastindex.org/namespace/1.0" version="2.0"><channel>
<item><guid>ep-new</guid><title>New one</title><link>https://x/new</link><pubDate>Mon, 19 Oct 2026 08:00:00 GMT</pubDate>
<podcast:transcript url="https://x/t.vtt" type="text/vtt"/></item>
<item><guid>ep-old</guid><title>Old one</title><link>https://x/old</link><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_sources_yaml_loads_and_is_tiered():
    sources, cap = load_sources("sources.yaml")
    assert cap == 2 and len(sources) >= 8
    assert {s.tier for s in sources} == {1, 2, 3}
    assert any(s.rss for s in sources) and any(s.youtube for s in sources)


def test_bad_tier_rejected(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("sources:\n  - {id: a, name: A, tier: 4, rss: x}\n")
    with pytest.raises(ValueError):
        load_sources(p)


def test_rss_window_uses_publisher_date_and_transcript_tag():
    src = SimpleNamespace(id="s", name="S", tier=1, rss="x")
    eps = from_rss(src, NOW, 7, parsed=feedparser.parse(RSS))
    assert [e.title for e in eps] == ["New one"]
    assert eps[0].published_at.day == 19
    assert eps[0].transcript_urls[0]["url"] == "https://x/t.vtt"


def test_canonical_id_prefers_guid():
    assert canonical_id("g", "vid", "l").startswith("rss:")
    assert canonical_id(None, "vid", "l") == "yt:vid"
    assert canonical_id("g", None, None) == canonical_id("g", None, "other")


def test_parse_date_formats():
    assert parse_date("Mon, 19 Oct 2026 08:00:00 GMT").tzinfo is not None
    assert parse_date("2026-10-19T08:00:00Z").hour == 8
    assert parse_date("garbage") is None


def test_store_dedupes_by_episode_id(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    ep = Episode("rss:1", "s", "S", 1, "t", "u", NOW)
    assert store.add_episode(ep, "x") is True
    assert store.add_episode(ep, "x") is False
    assert store.seen("rss:1")


def test_parse_vtt_and_text_with_timestamps():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v>Hello there</v>\n\n00:01:05.500 --> 00:01:07.000\nSecond line"
    segs = parse_cues(vtt)
    assert [s["start"] for s in segs] == [1.0, 65.5]
    omny = "00:00:02\nSpeaker 1: Bloomberg Audio.\n\n00:00:04\nSpeaker 2: Hi."
    segs = parse_cues(omny)
    assert segs[0]["speaker"] == "Speaker 1" and segs[1]["text"] == "Hi."


def test_parse_podcast_json():
    segs = parse_json('{"segments": [{"startTime": 3.5, "body": "Hi", "speaker": "Joe"}]}')
    assert segs == [{"start": 3.5, "speaker": "Joe", "text": "Hi"}]


def test_timestamps():
    assert to_seconds("1:02:03") == 3723
    assert fmt_ts(3723) == "1:02:03"


def test_title_match():
    assert title_match("Jensen Huang on AI capex", "Jensen Huang | AI Capex & more") >= 0.6
    assert title_match("Seinfeld", "Nvidia earnings") == 0




def test_holdings_parsing_and_relevance():
    data = {"holdings": [{"ticker": "aaa", "market_pct": 10.0}, {"ticker": "BBB", "market_pct": -5.0}]}
    w = holdings._weights(data)
    assert w == {"AAA": 0.10, "BBB": 0.05}
    assert holdings.relevance(5, ["AAA"], w) == pytest.approx(5.5)
    assert holdings.relevance(5, ["ZZZ"], w) == 5


