from datetime import datetime, timezone

import pytest

from podcast_alpha.ingest import Episode
from podcast_alpha.people import Person, dedupe, is_clip, load_people, mentions, parse_itunes, youtube_candidates

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
JH = Person("Jensen Huang", "NVIDIA CEO", "core")
CC = Person("C.C. Wei", "TSMC CEO", "core", ["CC Wei"])


def ep(id, title, src="person:Jensen Huang"):
    return Episode(id=id, source_id=src, source_name="S", tier=2, title=title, url="u", published_at=NOW)


def test_people_yaml_loads_without_holdings():
    people = load_people("people.yaml")
    assert len(people) == 78 and {p.priority for p in people} == {"core", "watch"}
    text = open("people.yaml").read().lower()
    assert not any(w in text for w in ("ticker", "weight", "holding", "shares"))


def test_bad_priority_and_duplicates_rejected(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text("people:\n  - {name: A, role: r, priority: hot}\n")
    with pytest.raises(ValueError):
        load_people(p)
    p.write_text("people:\n  - {name: A, role: r, priority: core}\n  - {name: A, role: r, priority: watch}\n")
    with pytest.raises(ValueError):
        load_people(p)


def test_mentions_uses_aliases_and_word_boundaries():
    assert mentions(CC, "An hour with CC Wei")
    assert mentions(CC, "", "TSMC's C.C. Wei on capacity")
    assert not mentions(JH, "Jensen Huangs")
    assert mentions(JH, "JENSEN HUANG | full interview")


@pytest.mark.parametrize("title,channel,desc,dur,clip", [
    ("Jensen Huang interview", "BG2", "", 3600, False),
    ("Jensen Huang interview", "BG2", "", 120, True),
    ("Jensen on China | Lex Fridman", "Lex Clips", "", 1800, True),
    ("Jensen Huang reacts", "X", "", 1800, True),
    ("Jensen Huang talk", "X", "Full video: https://youtu.be/x", 1800, True),
])
def test_is_clip(title, channel, desc, dur, clip):
    assert is_clip(title, channel, desc, dur, 20) is clip


def test_youtube_candidates_filters_name_and_clips():
    rows = [{"id": "a", "title": "Jensen Huang interview", "duration": 3000},
            {"id": "b", "title": "Lisa Su interview", "duration": 3000},
            {"id": "c", "title": "Jensen Huang #shorts", "duration": 3000}]
    assert [r["id"] for r in youtube_candidates(JH, 20, rows)] == ["a"]


def test_parse_itunes_window_length_and_tag():
    rows = [{"trackName": "Jensen Huang on AI", "collectionName": "Show", "releaseDate": "2026-10-19T08:00:00Z",
             "episodeGuid": "g1", "trackTimeMillis": 3_000_000, "episodeUrl": "https://a.mp3", "trackViewUrl": "https://ap"},
            {"trackName": "Jensen Huang old", "releaseDate": "2026-09-01T08:00:00Z", "episodeGuid": "g2", "trackTimeMillis": 3_000_000},
            {"trackName": "Jensen Huang short", "releaseDate": "2026-10-19T08:00:00Z", "episodeGuid": "g3", "trackTimeMillis": 60_000},
            {"trackName": "Other guest", "releaseDate": "2026-10-19T08:00:00Z", "episodeGuid": "g4", "trackTimeMillis": 3_000_000}]
    out = parse_itunes(JH, rows, NOW, 7)
    assert len(out) == 1
    e = out[0]
    assert e.source_id == "person:Jensen Huang" and e.source_name == "Show" and e.audio_url == "https://a.mp3"
    assert e.id.startswith("rss:")


def test_itunes_guid_matches_channel_rss_id():
    from podcast_alpha.ingest import canonical_id
    out = parse_itunes(JH, [{"trackName": "Jensen Huang", "releaseDate": "2026-10-19T08:00:00Z", "episodeGuid": "same",
                             "trackTimeMillis": 3_000_000}], NOW, 7)
    assert out[0].id == canonical_id("same", None, None)


def test_dedupe_against_channels_and_across_people():
    channel = [ep("rss:1", "Jensen Huang | Does AI Revenue Justify Capex", "bg2")]
    people = [ep("rss:1", "x"),
              ep("yt:2", "Jensen Huang | Does AI Revenue Justify Capex"),
              ep("yt:3", "Huang and Su on stage at the summit"),
              ep("yt:3", "Huang and Su on stage at the summit", "person:Lisa Su"),
              ep("yt:4", "Huang and Su on stage at the summit (full)", "person:Lisa Su")]
    kept = dedupe(channel, people)
    assert [e.id for e in kept] == ["yt:3"]
    assert kept[0].source_id == "person:Jensen Huang,person:Lisa Su"


def test_store_known_since_excludes_people(tmp_path):
    from datetime import timedelta
    from podcast_alpha.store import Store
    s = Store(tmp_path / "db.sqlite")
    s.add_episode(ep("yt:a", "Channel ep", "bg2"), None)
    s.add_episode(ep("rss:b", "Person ep"), None)
    assert [k.id for k in s.known_since(NOW - timedelta(days=1))] == ["yt:a"]
