import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from podcast_alpha.fetch import ep_from_dict, ep_to_dict, merge
from podcast_alpha.ingest import Episode
from podcast_alpha.store import Store

NOW = datetime.now(timezone.utc)
SEGS = [{"start": 0.0, "speaker": None, "text": "hi"}, {"start": 600.0, "speaker": None, "text": "bye"}]


def ep(id, title, src):
    return Episode(id=id, source_id=src, source_name="S", tier=2, title=title, url="u", published_at=NOW, youtube_id=id[3:])


def write_out(tmp_path, eps):
    d = tmp_path / "out"
    d.mkdir()
    (d / "episodes.json").write_text(json.dumps({"days": 7, "episodes": [ep_to_dict(e, s, t) for e, s, t in eps]}))
    return d


def test_roundtrip():
    e = ep("yt:a", "T", "bg2")
    back, segs, tsrc = ep_from_dict(json.loads(json.dumps(ep_to_dict(e, SEGS, "youtube-captions"))))
    assert back == e and segs == SEGS and tsrc == "youtube-captions"


def test_merge_dedupes_tags_and_writes(tmp_path):
    db = tmp_path / "db.sqlite"
    Store(db).add_episode(ep("yt:old", "Already stored", "bg2"), None)
    run = tmp_path / "run"
    out = write_out(tmp_path, [
        (ep("yt:old", "Already stored", "bg2"), None, None),
        (ep("yt:new", "Channel new ep", "semianalysis"), SEGS, "youtube-captions"),
        (ep("yt:p1", "Jensen Huang long interview on GPUs", "person:Jensen Huang"), SEGS, "youtube-captions"),
        (ep("yt:p1", "Jensen Huang long interview on GPUs", "person:Lisa Su"), SEGS, "youtube-captions"),
        (ep("yt:p2", "Channel new ep", "person:Lisa Su"), None, None),
    ])
    merge(SimpleNamespace(dir=str(out), into=str(run), db=str(db)))
    m = json.loads((run / "manifest.json").read_text())
    got = {e["episode_id"]: e for e in m["episodes"]}
    assert set(got) == {"yt:new", "yt:p1"}
    assert got["yt:p1"]["source_tag"] == "person:Jensen Huang,person:Lisa Su" and got["yt:p1"]["via"] == "youtube"
    assert got["yt:new"]["via"] == "channel" and got["yt:new"]["transcript_minutes"] == 10
    assert (run / got["yt:p1"]["file"]).exists()
    merge(SimpleNamespace(dir=str(out), into=str(run), db=str(db)))
    assert len(json.loads((run / "manifest.json").read_text())["episodes"]) == 2


def test_youtube_only_path_needs_no_feedparser_or_requests():
    code = ("import sys; sys.modules['feedparser'] = None; sys.modules['requests'] = None; "
            "from podcast_alpha.cli import main; from podcast_alpha import fetch, people, ingest")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
