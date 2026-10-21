from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import feedparser
import pytest

from podcast_alpha import holdings, llm
from podcast_alpha.config import load_sources
from podcast_alpha.digest import render
from podcast_alpha.extract import heuristic_claims, novelty, sentences
from podcast_alpha.ingest import Episode, canonical_id, from_rss, parse_date
from podcast_alpha.notion import md_to_blocks, rich_text
from podcast_alpha.score import force_spread
from podcast_alpha.store import Store
from podcast_alpha.tickers import find_tickers
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
    store.add_claims("rss:1", [{"claim": "Nvidia revenue rose 50%", "ticker": ["NVDA"]}])
    assert store.recent_claims() == ["Nvidia revenue rose 50%"]
    assert store.recent_claims(exclude_episode="rss:1") == []


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


def test_find_tickers_and_ambiguous_words():
    assert find_tickers("Nvidia and TSMC beat; $AMD too") == ["NVDA", "TSM", "AMD"]
    assert find_tickers("my arm hurts, very meta") == []


def test_sentences_span_caption_snippets():
    segs = [{"start": 0, "text": "Nvidia revenue grew"}, {"start": 2, "text": "50% last quarter. Next"},
            {"start": 4, "text": "point is here now ok."}]
    out = list(sentences(segs))
    assert out[0] == (0, None, "Nvidia revenue grew 50% last quarter.")


def test_heuristic_claims_schema_and_ads_skipped():
    segs = [{"start": 10, "text": "Microsoft capex will reach $80 billion next year."},
            {"start": 20, "text": "This episode is brought to you by Nvidia, use code 50% off."},
            {"start": 30, "text": "I really love this show and the vibes."}]
    claims = heuristic_claims(segs, [])
    assert len(claims) == 1
    assert set(claims[0]) == {"ticker", "claim", "speaker", "timestamp", "novelty", "verifiable"}
    assert claims[0]["ticker"] == ["MSFT"] and claims[0]["timestamp"] == 10


def test_novelty_against_prior():
    assert novelty("Microsoft capex will reach eighty billion", ["Microsoft capex will reach eighty billion"]) == 0
    assert novelty("Microsoft capex", []) == 3


def test_force_spread_meets_distribution():
    scores = force_spread([float(i) for i in range(20)])
    assert max(scores) == 10 and min(scores) == 1
    assert sum(s >= 8 for s in scores) >= 2
    assert sum(s <= 4 for s in scores) >= 6
    assert force_spread([1.0, 1.0]) == [force_spread([1.0, 1.0])[0]] * 2
    assert force_spread([3.0]) == [5]


def test_holdings_parsing_and_relevance():
    data = {"holdings": [{"ticker": "aaa", "market_pct": 10.0}, {"ticker": "BBB", "market_pct": -5.0}]}
    w = holdings._weights(data)
    assert w == {"AAA": 0.10, "BBB": 0.05}
    assert holdings.relevance(5, ["AAA"], w) == pytest.approx(5.5)
    assert holdings.relevance(5, ["ZZZ"], w) == 5


def test_provider_detection_reads_names_only(monkeypatch):
    for env, *_ in llm.PROVIDERS:
        monkeypatch.delenv(env, raising=False)
    assert llm.available_provider() is None
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert llm.available_provider()[1] == "openai"


def test_render_and_notion_blocks():
    ep = {"title": "T", "url": "https://u", "source": "S", "tier": 1, "published": "2026-10-19", "score": 7,
          "reason": "why", "transcript_source": "x", "youtube_id": "vid",
          "claims": [{"score": 8, "ticker": ["NVDA"], "claim": "c", "speaker": "unknown", "timestamp": 61,
                      "novelty": 2, "verifiable": 3}]}
    md = render([ep], {"week_of": "2026-10-14", "days": 7, "run_at": "now", "extractor": "h", "holdings": "off"})
    assert "Week of 2026-10-14" in md and "t=61s" in md
    blocks = md_to_blocks(md)
    assert any(b["type"] == "table" for b in blocks)
    assert any(b["type"] == "bulleted_list_item" for b in blocks)
    rt = rich_text("see [x](https://a) **b**")
    assert rt[1]["text"]["link"]["url"] == "https://a" and rt[3]["annotations"]["bold"]
