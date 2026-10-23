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
