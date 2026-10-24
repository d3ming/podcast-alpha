import json

import yaml

from podcast_alpha import links, showlinks
from podcast_alpha.digest import render, repeat_notes

APPLE = "https://podcasts.apple.com/us/podcast/plain-english/id1594471023"
LOOKUP = {"results": [
    {"wrapperType": "track", "collectionName": "Plain English"},
    {"wrapperType": "podcastEpisode", "trackName": "The AI Story Wall Street Doesn't Want to Hear", "releaseDate": "2026-10-23T08:00:00Z",
     "trackViewUrl": "https://podcasts.apple.com/us/podcast/the-ai-story/id1594471023?i=111&uo=4"},
    {"wrapperType": "podcastEpisode", "trackName": "The AI Story Wall Street Doesn't Want to Hear", "releaseDate": "2026-08-01T08:00:00Z",
     "trackViewUrl": "https://podcasts.apple.com/us/podcast/older/id1594471023?i=222&uo=4"},
    {"wrapperType": "podcastEpisode", "trackName": "Something else entirely", "releaseDate": "2026-10-23T08:00:00Z",
     "trackViewUrl": "https://podcasts.apple.com/us/podcast/other/id1594471023?i=333"},
]}


def test_match_itunes_episode_needs_title_and_date():
    eps = [r for r in LOOKUP["results"] if r["wrapperType"] == "podcastEpisode"]
    title = "The AI Story Wall Street Doesn’t Want to Hear"
    assert links.match_itunes_episode(title, "2026-10-23", eps) == "https://podcasts.apple.com/us/podcast/the-ai-story/id1594471023?i=111"
    assert links.match_itunes_episode(title, "2026-10-21", eps).endswith("i=111")  # within 2 days
    assert links.match_itunes_episode(title, "2026-10-26", eps) is None  # same title, wrong week
    assert links.match_itunes_episode(title, None, eps) is None  # no date, no guess
    assert links.match_itunes_episode("Unrelated episode about bonds", "2026-10-23", eps) is None


def test_itunes_lookup_cached_per_collection():
    calls = []

    def get(url):
        calls.append(url)
        return LOOKUP

    cache = {}
    for _ in range(3):
        assert links.apple_show_episode("The AI Story Wall Street Doesn't Want to Hear", "2026-10-23", APPLE, get, cache)
    assert len(calls) == 1 and "id=1594471023" in calls[0] and "entity=podcastEpisode" in calls[0]
    assert links.apple_show_episode("x", "2026-10-23", "https://example.com/no-id", get, cache) is None


def test_failed_lookup_is_cached_empty():
    calls = []

    def get(url):
        calls.append(url)
        raise OSError("down")

    cache = {}
    assert links.apple_show_episode("t", "2026-10-23", APPLE, get, cache) is None
    assert links.apple_show_episode("t", "2026-10-23", APPLE, get, cache) is None
    assert len(calls) == 1


def test_is_homepage():
    assert links.is_homepage("https://www.theringer.com/")
    assert links.is_homepage("http://semidoped.fm")
    assert links.is_homepage("https://www.bigtechnology.com")
    assert links.is_homepage("https://www.dwarkesh.com/podcast", "https://dwarkesh.com/podcast/")  # equals the show's site
    assert not links.is_homepage("https://www.dwarkesh.com/p/intel-tour", "https://www.dwarkesh.com/podcast")
    assert not links.is_homepage("https://www.theringer.com/podcasts/plain-english/2026/10/23/x")
    assert not links.is_homepage("")


def test_episode_urls_replaces_only_homepage_missing_or_tracking():
    shows = {"plain english": {"site": "https://www.theringer.com/", "apple": APPLE}, "ok show": {"apple": APPLE}}
    eps = [
        {"rank": 1, "episode_id": "a", "source": "Plain English", "title": "The AI Story Wall Street Doesn't Want to Hear",
         "published": "2026-10-23", "url": "https://www.theringer.com/"},
        {"rank": 2, "episode_id": "b", "source": "Plain English", "title": "The AI Story Wall Street Doesn't Want to Hear",
         "published": "2026-10-23", "url": ""},
        {"rank": 3, "episode_id": "c", "source": "OK Show", "title": "The AI Story Wall Street Doesn't Want to Hear",
         "published": "2026-10-23", "url": "https://ok.example/episodes/1"},
        {"rank": 4, "episode_id": "d", "source": "Plain English", "title": "No such episode", "published": "2026-10-23", "url": ""},
    ]
    changed = links.episode_urls(eps, shows, get=lambda u: LOOKUP)
    assert [c[0] for c in changed] == [1, 2]
    assert eps[0]["url"].endswith("?i=111") and eps[2]["url"] == "https://ok.example/episodes/1" and eps[3]["url"] == ""


def test_fill_uses_itunes_when_page_is_homepage(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    ep = {"episode_id": "e1", "show": "Plain English", "title": "The AI Story Wall Street Doesn't Want to Hear", "published": "2026-10-23",
          "url": "https://www.theringer.com/"}
    (run / "selected.json").write_text(json.dumps({"episodes": [ep]}))
    (run / "manifest.json").write_text(json.dumps({"episodes": [{**ep, "source": "Plain English"}]}))
    sl = tmp_path / "show_links.yaml"
    sl.write_text(yaml.safe_dump({"pe": {"name": "Plain English", "site": "https://www.theringer.com/", "youtube": "", "apple": APPLE, "spotify": ""}}))

    def get(url):
        if "lookup" in url:
            return LOOKUP
        return {"results": []}

    out = links.fill(run, None, get=get, fetch=lambda u, **k: (_ for _ in ()).throw(OSError("no net")), show_links=sl)
    assert out["e1"][1].endswith("?i=111")
    assert json.loads((run / "selected.json").read_text())["episodes"][0]["listen_url"].endswith("?i=111")


# --- verify mode ---

def test_classify_youtube():
    feed = ["Alpha deep dive with Jane Doe", "Beta market outlook", "Gamma chips and power", "Delta bonds"]
    st, _ = showlinks.classify_youtube(["Alpha deep dive with Jane Doe", "Beta market outlook!", "random clip"], feed)
    assert st == "ok"
    st, ev = showlinks.classify_youtube(["Cooking pasta", "Cat videos", "Guitar lesson"], feed)
    assert st == "mismatch" and "0/3" in ev
    assert showlinks.classify_youtube([], feed)[0] == "unreachable"  # nothing to judge from
    assert showlinks.classify_youtube(["Cooking pasta", "Cat videos"], ["only one"])[0] == "unreachable"


def test_name_like_is_stricter_than_similar():
    assert showlinks.name_like("Apple Events", "Apple")
    assert showlinks.name_like("Sourcery", "Sourcery with Molly O'Shea")
    assert not showlinks.name_like("Supply Chained", "Supply Chain Now")
    assert not showlinks.name_like("Big Technology Podcast", "Bloomberg Podcasts")


def _verify_setup(tmp_path, channels):
    src = tmp_path / "sources.yaml"
    srcs = [{"id": k, "name": v["name"], "rss": f"https://feed/{k}"} for k, v in channels.items()]
    src.write_text(yaml.safe_dump({"sources": srcs}))
    path = tmp_path / "show_links.yaml"
    path.write_text(yaml.safe_dump({k: {"name": v["name"], "site": "", "youtube": f"https://www.youtube.com/channel/UC{k * 22}",
                                        "apple": "", "spotify": ""} for k, v in channels.items()}))
    return src, path


def _chan_xml(chan, titles):
    return f"<feed><title>{chan}</title>" + "".join(f"<entry><title>{t}</title></entry>" for t in titles) + "</feed>"


def test_verify_never_edits_without_fix_and_fix_blanks_only_mismatch(tmp_path):
    chans = {"a": {"name": "Show A"}, "b": {"name": "Show B"}, "c": {"name": "Show C"}, "d": {"name": "Show D"}}
    src, path = _verify_setup(tmp_path, chans)
    before = path.read_text()
    feed_titles = ["Episode one about chips", "Episode two about power", "Episode three about bonds"]
    xml = {"a": _chan_xml("Show A", feed_titles),
           "b": _chan_xml("Other Network", ["Cooking pasta", "Cat videos", "Guitar lesson"]),   # wrong channel
           "c": _chan_xml("Show C", ["Cooking pasta", "Cat videos", "Guitar lesson"])}        # right name, retitled videos

    def yt(url):
        k = url.split("UC")[1][0]
        if k == "d":
            raise OSError("timeout")
        return xml[k]

    class Feed:
        entries = [{"title": t} for t in feed_titles]

    rows = showlinks.verify(src, path, yt_fetch=yt, parse=lambda u: Feed(), sleep=lambda s: None)
    status = {r["id"]: r["status"] for r in rows}
    assert status == {"a": "ok", "b": "mismatch", "c": "mismatch", "d": "unreachable"}
    assert path.read_text() == before  # verify alone edits nothing
    blanked = showlinks.apply_fix(path, rows)
    assert [r["id"] for r in blanked] == ["b"]  # c is named like the show, d is unreachable
    data = showlinks.load(path)
    assert data["b"]["youtube"] == "" and data["a"]["youtube"] and data["c"]["youtube"] and data["d"]["youtube"]


def test_verify_stops_asking_youtube_after_block(tmp_path):
    chans = {"a": {"name": "Show A"}, "b": {"name": "Show B"}}
    src, path = _verify_setup(tmp_path, chans)
    calls = []

    def yt(url):
        calls.append(url)
        raise showlinks.Blocked("HTTP 429")

    class Feed:
        entries = [{"title": "x"}, {"title": "y"}]

    rows = showlinks.verify(src, path, yt_fetch=yt, parse=lambda u: Feed(), sleep=lambda s: None)
    assert len(calls) == 1 and [r["status"] for r in rows] == ["unreachable", "unreachable"]
    assert showlinks.apply_fix(path, rows) == []


def test_spotify_candidates_from_feed_and_embed_forms():
    a, b = "a" * 22, "b" * 22
    text = f"x open.spotify.com/show/{b} y open.spotify.com/embed/show/{a} z spotify:show:{a} open.spotify.com/show/{a}?si=1"
    assert showlinks.spotify_candidates(text) == [f"https://open.spotify.com/show/{a}", f"https://open.spotify.com/show/{b}"]


def test_discover_finds_spotify_in_feed_xml_with_title_check():
    good, bad = "g" * 22, "x" * 22
    pages = {"https://feed/x": f"<rss>open.spotify.com/show/{bad} open.spotify.com/show/{good} open.spotify.com/show/{good}</rss>",
             f"https://open.spotify.com/show/{good}": "<title>Show X | Listen to the Podcast on Spotify</title>",
             f"https://open.spotify.com/show/{bad}": "<title>Another Show | Listen to the Podcast on Spotify</title>"}
    got = showlinks.discover({"id": "x", "name": "Show X", "rss": "https://feed/x"}, get=lambda u: {"results": []},
                             fetch=lambda u: pages[u], parse=lambda u: type("F", (), {"feed": {}})())
    assert got["spotify"].endswith(good)


# --- digest ---

def _ep(rank, title, speaker, source="Show"):
    return {"rank": rank, "episode_id": f"e{rank}", "title": title, "url": "https://x/" + str(rank), "source": source, "tier": 1,
            "published": "2026-10-20", "score": 8, "reason": "r",
            "claims": [{"ticker": [], "claim": "c", "speaker": speaker, "timestamp": 5, "novelty": 1, "verifiable": 1}]}


def test_digest_notes_repeat_guest():
    eps = [_ep(1, "Sundar Pichai on Gemini 4", "Sundar Pichai"), _ep(2, "Unrelated chips talk", "host (Jane Roe)"),
           _ep(3, "Google CEO Sundar Pichai: AI", "Sundar Pichai (Alphabet CEO)"), _ep(4, "Another", "Sundar Pichai (Alphabet CEO)")]
    notes = repeat_notes(eps)
    assert notes == {2: ["Also #1: another interview with Sundar Pichai"], 3: ["Also #1, #3: another interview with Sundar Pichai"]}
    md = render({"week_of": "2026-10-17", "ranked_by": "t", "run_dir": "r", "episodes": eps})
    assert md.count("another interview with Sundar Pichai") == 2
    assert md.index("## 1.") < md.index("Also #1: another") and "Also" not in md.split("## 2.")[0]


# --- Apple episode matching: identity first, strict title otherwise ---

def _ap(name, date, i, **kw):
    return {"wrapperType": "podcastEpisode", "trackName": name, "releaseDate": f"{date}T08:00:00Z",
            "trackViewUrl": f"https://podcasts.apple.com/us/podcast/x/id1?i={i}", **kw}


def test_match_does_not_pick_neighbouring_daily_episode():
    eps = [_ap("TBPN | Thursday, October 22", "2026-10-22", 1)]
    assert links.match_itunes_episode("TBPN | Friday, October 23", "2026-10-23", eps) is None


def test_match_does_not_confuse_parts():
    eps = [_ap("Sundar Pichai, Part 2", "2026-10-23", 2)]
    assert links.match_itunes_episode("Sundar Pichai, Part 1", "2026-10-23", eps) is None
    eps.append(_ap("Sundar Pichai, Part 1", "2026-10-23", 1))
    assert links.match_itunes_episode("Sundar Pichai, Part 1", "2026-10-23", eps).endswith("i=1")


def test_match_prefers_smallest_date_gap_for_same_title():
    eps = [_ap("Daily Briefing", "2026-10-25", 25), _ap("Daily Briefing", "2026-10-24", 24)]
    assert links.match_itunes_episode("Daily Briefing", "2026-10-23", eps).endswith("i=24")  # closest, not newest
    eps.append(_ap("Daily Briefing", "2026-10-23", 23))
    assert links.match_itunes_episode("Daily Briefing", "2026-10-23", eps).endswith("i=23")


def test_match_by_identity_guid_and_enclosure():
    import hashlib

    guid = "tag:example.com,2026:ep-9"
    eid = "rss:" + hashlib.sha1(guid.encode()).hexdigest()[:16]
    eps = [_ap("Retitled for Apple", "2026-10-20", 7, episodeGuid=guid),
           _ap("Other", "2026-10-23", 8, episodeGuid="different")]
    assert links.match_itunes_episode("Original title", "2026-10-23", eps, ident={"episode_id": eid}).endswith("i=7")
    assert links.match_itunes_episode("Original title", "2026-10-23", eps, ident={"episode_id": "rss:other"}) is None
    eps = [_ap("Renamed", "2026-10-20", 5, episodeUrl="https://cdn.example.com/show/ep5.mp3?x=1")]
    ident = {"episode_id": "rss:z", "audio_url": "https://dts.podtrac.com/redirect.mp3/cdn.example.com/show/ep5.mp3?source=feed"}
    assert links.match_itunes_episode("Original", "2026-10-23", eps, ident=ident).endswith("i=5")
    assert links.audio_key("https://chrt.fm/track/ABC/pdst.fm/e/cdn.example.com/show/ep5.mp3") == "cdn.example.com/show/ep5.mp3"


def test_digest_no_repeat_note_for_hosts():
    def ep(rank, title, speaker):
        return {"rank": rank, "title": title, "source": "All-In", "claims": [{"speaker": s} for s in speaker]}
    eps = [ep(1, "Ep A", ["Chamath Palihapitiya (host)", "Jane Roe (CEO, Acme)"]),
           ep(2, "Ep B", ["Chamath Palihapitiya (co-host)", "Jane Roe (CEO, Acme)"]),
           ep(3, "Ep C", ["Chamath Palihapitiya", "Bob Lee (founder)"])]  # host elsewhere, bare name here
    # hosts (even bare-named in a later episode) get no note; the real guest repeat does
    assert repeat_notes(eps) == {1: ["Also #1: another interview with Jane Roe"]}
