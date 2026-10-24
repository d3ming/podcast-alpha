import json

import yaml

from podcast_alpha import showlinks
from podcast_alpha.digest import show_line

SRC = {"id": "x", "name": "Show X", "rss": "https://feed/x", "youtube_channel_id": "UCabc"}


def test_discover_uses_known_channel_apple_site_and_skips_host_youtube():
    def get(url):
        return {"results": [{"feedUrl": "https://feed/x", "collectionName": "Show X",
                             "collectionViewUrl": "https://podcasts.apple.com/us/podcast/x/id1?uo=4"}]}

    page = 'a <a href="https://www.youtube.com/@spotifyforcreators">y</a> <a href="https://open.spotify.com/show/' + "a" * 22 + '">s</a>'
    pages = {"https://host.buzzsprout.com/1": page, "https://open.spotify.com/show/" + "a" * 22: "<title>Show X | Listen to the Podcast on Spotify</title>"}
    feed = type("F", (), {"feed": {"link": "https://host.buzzsprout.com/1"}})()
    got = showlinks.discover({**SRC, "youtube_channel_id": None}, get=get, fetch=lambda u: pages[u], parse=lambda u: feed)
    assert got["apple"] == "https://podcasts.apple.com/us/podcast/x/id1"
    assert got["youtube"] == ""  # hosting platform pages must not donate their own channel
    assert got["spotify"].endswith("a" * 22)
    assert showlinks.discover(SRC, get=get, fetch=lambda u: pages[u], parse=lambda u: feed)["youtube"].endswith("UCabc")


def test_spotify_rejected_when_page_title_differs():
    assert not showlinks.spotify_ok("u", "Show X", lambda u: "<title>Completely Different Pod | Listen to the Podcast on Spotify</title>")


def test_fill_never_overwrites_and_fills_blanks(tmp_path):
    src = tmp_path / "sources.yaml"
    src.write_text(yaml.safe_dump({"sources": [SRC, {"id": "calls-y", "name": "Y call", "rss": "r"}]}))
    path = tmp_path / "show_links.yaml"
    path.write_text(yaml.safe_dump({"x": {"name": "Show X", "site": "https://mine", "youtube": "", "apple": "", "spotify": ""}}))
    changed = showlinks.fill(src, path, discover_fn=lambda s: {"site": "https://auto", "youtube": "https://yt", "apple": "", "spotify": ""})
    row = showlinks.load(path)["x"]
    assert row["site"] == "https://mine" and row["youtube"] == "https://yt" and row["apple"] == ""
    assert changed == {"x": ["youtube"]} and "calls-y" not in showlinks.load(path)
    assert showlinks.by_name(path)["show x"]["youtube"] == "https://yt"


def test_show_line_skips_blank_fields():
    assert show_line({"site": "https://s", "youtube": "", "apple": "https://a", "spotify": ""}) == "Show: [Site](https://s) · [Apple](https://a)"
    assert show_line({}) == ""


def test_transcript_link_prefers_recorded_url_then_youtube_captions():
    from podcast_alpha.links import transcript_label, transcript_link

    assert transcript_link({"transcript_url": "https://cdn.example.com/t/1.html"}) == "https://cdn.example.com/t/1.html"
    assert transcript_link({"transcript_url": "https://x/ep.mp3"}) is None
    row = {"transcript_source": "youtube-captions (mirror)", "youtube_id": "abcdefghijk"}
    assert transcript_link(row) == "https://www.youtube.com/watch?v=abcdefghijk"
    assert transcript_link({"transcript_source": "whisper (30 min)", "youtube_id": "abcdefghijk"}) is None
    assert transcript_label("https://www.youtube.com/watch?v=abcdefghijk") == "YouTube captions"
    assert transcript_label("https://cdn.example.com/t.html") == "Publisher transcript"


def test_from_rss_tag_reports_winning_url(monkeypatch):
    import requests

    from podcast_alpha import transcribe

    class R:
        def __init__(self, text):
            self.text = text
        def raise_for_status(self):
            pass

    def get(url, **kw):
        if "bad" in url:
            raise requests.RequestException()
        return R("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello world\n")

    monkeypatch.setattr(requests, "get", get)
    used = []
    segs = transcribe.from_rss_tag([{"url": "https://bad/1"}, {"url": "https://ok/2.vtt", "type": "text/vtt"}], used)
    assert segs and used == ["https://ok/2.vtt"]


def test_digest_and_report_show_transcript_link():
    from podcast_alpha.digest import render
    from tests.test_ranked import GOOD

    d = json.loads(json.dumps(GOOD))
    d["episodes"][0]["transcript_url"] = "https://cdn.example.com/t.html"
    assert "Transcript: [Publisher transcript](https://cdn.example.com/t.html)" in render(d)


def test_backfill_transcript_urls_matches_by_title(tmp_path):
    from podcast_alpha.links import backfill_transcript_urls

    (tmp_path / "manifest.json").write_text(json.dumps({"episodes": [
        {"episode_id": "a", "title": "Episode One", "source_tag": "x", "transcript_source": "podcast:transcript"},
        {"episode_id": "b", "title": "Unrelated", "source_tag": "x", "transcript_source": "podcast:transcript"}]}))
    src = tmp_path / "sources.yaml"
    src.write_text(yaml.safe_dump({"sources": [{"id": "x", "name": "X", "rss": "https://feed/x"}]}))
    entry = {"title": "Episode One", "links": [{"rel": "transcript", "href": "https://t/1.vtt", "type": "text/vtt"}]}
    feed = type("F", (), {"entries": [type("E", (dict,), {})(entry)]})()
    assert backfill_transcript_urls(tmp_path, src, parse=lambda u: feed) == {"missing": 2, "found": 1}
    rows = {r["episode_id"]: r for r in json.loads((tmp_path / "manifest.json").read_text())["episodes"]}
    assert rows["a"]["transcript_url"] == "https://t/1.vtt" and "transcript_url" not in rows["b"]
