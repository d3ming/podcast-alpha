import copy
import json

import pytest

from podcast_alpha import links, report
from podcast_alpha.ranked import RankedError
from tests.test_daily import REPORT

EP = {"title": "The New Apex Predator: CrowdStrike CEO George Kurtz on Adversaries", "show": "No Priors",
      "published": "2026-10-22", "guests": [{"name": "George Kurtz", "role": "CEO"}, {"name": "Sarah Guo", "role": "host"}]}


@pytest.mark.parametrize("url", ["https://traffic.megaphone.fm/PDP1.mp3", "https://dts.podtrac.com/redirect.mp3/x",
                                 "https://chrt.fm/track/x/ep", "https://pdst.fm/e/x", "https://a.com/ep.m4a?x=1", "ftp://x"])
def test_bad_urls(url):
    assert links.bad_url(url)


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abcdefghijk", "https://podcasts.apple.com/us/podcast/x/id1?i=2",
                                 "https://open.spotify.com/episode/x", "https://allinchamathjason.libsyn.com/some-episode"])
def test_good_urls(url):
    assert links.bad_url(url) is None


def test_yt_matching_strict():
    assert links.yt_matches(EP, "The New Apex Predator: CrowdStrike CEO George Kurtz on Adversaries Armed With AI Agents", "x", "2026-10-22T10:00:00Z")
    assert not links.yt_matches(EP, "The New Apex Predator: CrowdStrike CEO George Kurtz on Adversaries", "x", "2026-09-01")  # date far
    assert links.yt_matches(EP, "George Kurtz on AI security", "No Priors: AI, Machine Learning", None)  # guest + own channel
    assert not links.yt_matches(EP, "George Kurtz on AI security", "CNBC Television", None)  # guest on another channel
    assert not links.yt_matches(EP, "Sarah Guo on startups", "No Priors", None)  # host name doesn't count


def test_yt_verify_rejects_wrong_video():
    fetch = lambda url, **k: json.dumps({"title": "Reid Hoffman on hype in AI", "author_name": "No Priors"})
    assert links.yt_verify(EP, "https://youtu.be/abcdefghijk", fetch=fetch) is None
    fetch = lambda url, **k: json.dumps({"title": EP["title"], "author_name": "No Priors"})
    assert links.yt_verify(EP, "https://youtu.be/abcdefghijk", fetch=fetch) == "https://www.youtube.com/watch?v=abcdefghijk"


def test_channel_rss_and_ddg_rate_limit():
    xml = ("<feed><entry><yt:videoId>zzzzzzzzzzz</yt:videoId><title>Other episode</title><published>2026-10-22</published></entry>"
           f"<entry><yt:videoId>abcdefghijk</yt:videoId><title>{EP['title']}</title><published>2026-10-22T09:00:00+00:00</published></entry></feed>")
    assert links.yt_channel_rss(EP, "UC1", fetch=lambda u, **k: xml) == "https://www.youtube.com/watch?v=abcdefghijk"
    assert links.yt_ddg(EP, fetch=lambda u, **k: "<html>anomaly detected</html>") is None


def test_apple_lookup_by_feed():
    def get(url):
        if "entity=podcast&" in url or url.endswith("entity=podcast"):
            return {"results": [{"collectionId": 9, "collectionName": "No Priors: AI", "feedUrl": "https://f/np"}]}
        if "lookup" in url:
            return {"results": [{"wrapperType": "track"}, {"wrapperType": "podcastEpisode", "trackName": EP["title"],
                                 "trackViewUrl": "https://podcasts.apple.com/us/podcast/x/id9?i=5&uo=4"}]}
        return {"results": []}
    assert links.apple_episode("No Priors", EP["title"], "https://f/np", get) == "https://podcasts.apple.com/us/podcast/x/id9?i=5"
    assert links.resolve({**EP, "url": "https://traffic.megaphone.fm/x.mp3"}, "https://f/np", get).startswith("https://podcasts.apple.com/")
    assert links.resolve({**EP, "url": "https://traffic.megaphone.fm/x.mp3"}, None, lambda u: {"results": []}) is None


def test_fill_writes_selected_and_manifest(tmp_path):
    (tmp_path / "selected.json").write_text(json.dumps({"episodes": [{**EP, "episode_id": "rss:1", "url": "https://x.com/ep",
                                                                      "youtube_url": "https://youtu.be/abcdefghijk"}]}))
    (tmp_path / "manifest.json").write_text(json.dumps({"episodes": [{"episode_id": "rss:1", "title": EP["title"]}]}))
    fetch = lambda url, **k: json.dumps({"title": "unrelated", "author_name": "Someone"}) if "oembed" in url else "anomaly"
    out = links.fill(tmp_path, None, get=lambda u: {"results": []}, fetch=fetch)
    sel = json.loads((tmp_path / "selected.json").read_text())["episodes"][0]
    assert out == {"rss:1": (None, "https://x.com/ep")} and "youtube_url" not in sel and sel["listen_url"] == "https://x.com/ep"
    assert json.loads((tmp_path / "manifest.json").read_text())["episodes"][0]["listen_url"] == "https://x.com/ep"


def test_render_prefers_youtube_then_listen_and_rejects_tracking(tmp_path):
    data = copy.deepcopy(REPORT)
    for e in data["episodes"]:
        e["url"] = "https://traffic.megaphone.fm/a.mp3"
    sel = [{"episode_id": "yt:a", "youtube_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
           {"episode_id": "yt:b", "listen_url": "https://podcasts.apple.com/us/podcast/x/id1?i=2"}, {"episode_id": "yt:c"}]
    (tmp_path / "selected.json").write_text(json.dumps({"episodes": sel}))
    report.attach_links(data, tmp_path)
    md = report.render(report.validate(data, "report"))
    assert "1. **[Jane on robots](https://www.youtube.com/watch?v=aaaaaaaaaaa)**" in md
    assert "2. **[Jane on robots](https://podcasts.apple.com/us/podcast/x/id1?i=2)**" in md
    assert "3. **Jane on robots** Widget Show" in md and ".mp3" not in md
    assert "## [Jane on robots](https://www.youtube.com/watch?v=aaaaaaaaaaa)" in md and "aaaaaaaaaaa&t=75s" in md
    bad = copy.deepcopy(REPORT)
    bad["essential"][0]["listen_url"] = "https://chrt.fm/track/x"
    with pytest.raises(RankedError, match="tracking"):
        report.validate(bad, "report")
