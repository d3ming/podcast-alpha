import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from podcast_alpha import fetch as fetch_mod
from podcast_alpha import notion, report, tmtb
from podcast_alpha.ingest import Episode
from podcast_alpha.ranked import RankedError

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
EMAIL = (Path(__file__).parent / "fixtures" / "reference_email.txt").read_text()
SIG = {"text": "t", "speaker": "Jane Placeholder", "timestamp": 75, "our_read": "r"}
REPORT = {"date": "2026-10-21", "title": "Podcast Intel: test", "run_dir": "runs/x", "key_insights": ["k1", "k2", "k3"],
          "essential": [{"episode_id": f"yt:{k}", "headline": "Jane on robots", "show": "Widget Show", "guests": "Host and Jane, CEO"}
                        for k in "abc"],
          "episodes": [{"episode_id": f"yt:{k}", "headline": "Jane on robots", "url": f"https://www.youtube.com/watch?v={k}",
                        "signal": [SIG, SIG, {**SIG, "timestamp": None}],
                        "sections": [{"title": "Demand", "body": "b", "claims": [{"text": "c", "speaker": "host", "timestamp": 3}]}]}
                       for k in "abc"]}


def pick(i):
    return {"episode_id": f"rss:{i}", "title": "T", "show": "S", "url": "u", "guests": [{"name": "A B", "role": "CEO"}],
            "kind": "exec", "why": "w"}


def test_parse_email_fixture():
    seeds = tmtb.parse_email(EMAIL)
    assert [s["n"] for s in seeds] == [1, 2, 3]
    assert seeds[0]["guests"] == [{"name": "Jane Placeholder", "role": "CEO of Acme Robotics"}]
    assert [g["name"] for g in seeds[1]["guests"]] == ["Omar Example"]
    assert [g["name"] for g in seeds[2]["guests"]] == ["Nadia Sample", "Leo Test"]
    assert tmtb.split_people("Ridge’s Connor Doe and Ann Roe, director at Acme")[0]["name"] == "Connor Doe"


def test_tmtb_run_writes_missed(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"episodes": [
        {"episode_id": "rss:1", "title": "Jane Placeholder on automation", "source": "Widget Show"}]}))
    rows = [{"trackName": "Omar Example on rates", "collectionName": "Market Hour", "releaseDate": "2026-10-20T08:00:00Z",
             "episodeGuid": "g", "trackTimeMillis": 3_000_000, "trackViewUrl": "https://ap", "episodeUrl": "https://a.mp3"}]
    out = tmtb.run(EMAIL, tmp_path, fetch_json=lambda term: rows if term == "Omar Example" else [], now=NOW)
    assert out["seeds"][0]["covered_by"] == ["rss:1"]
    assert [s["n"] for s in out["missed"]] == [2, 3]
    assert out["missed"][0]["candidates"][0]["show"] == "Market Hour"
    md = (tmp_path / "missed.md").read_text()
    assert "2 of 3" in md and "no free audio found" in md
    assert json.loads((tmp_path / "seeds.json").read_text())[1]["guests"][0]["name"] == "Omar Example"


def test_report_validates_and_renders():
    md = report.render(report.validate(copy.deepcopy(REPORT), "report"))
    assert md.index("## Key Insights") < md.index("- k1") < md.index("## Essential Episodes")
    assert "## Essential Episodes" in md and "1. **Jane on robots** Widget Show" in md
    assert "### THE SIGNAL" in md and "### 01 Demand" in md
    assert "- t [Jane Placeholder, [0:01:15](https://www.youtube.com/watch?v=a&t=75s)] (our read: r)" in md
    assert "- t [Jane Placeholder] (our read: r)" in md


@pytest.mark.parametrize("mutate", [
    lambda d: d["episodes"][0]["signal"][0].pop("speaker"),
    lambda d: d["episodes"][0]["signal"].extend([SIG, SIG]),
    lambda d: d["episodes"][0].update(episode_id="yt:zzz"),
    lambda d: d["episodes"].pop(),
    lambda d: d.pop("key_insights"),
    lambda d: d.update(key_insights=["a", "b"]),
    lambda d: d.update(key_insights=["a"] * 6),
    lambda d: d["essential"].append(dict(d["essential"][0])),
    lambda d: d["essential"][0].update(guests="holding 5%") or d.update(title="x") or d["episodes"][0]["sections"][0].update(weight=1),
])
def test_report_rejects(mutate):
    d = copy.deepcopy(REPORT)
    mutate(d)
    with pytest.raises(RankedError):
        report.validate(d, "report")


def test_selected_schema_bounds():
    good = {"date": "2026-10-21", "run_dir": "runs/x", "selected_by": "Cain", "episodes": [pick(i) for i in range(3)]}
    report.validate(good, "selected")
    for n in (2, 4):
        with pytest.raises(RankedError):
            report.validate({**good, "episodes": [pick(i) for i in range(n)]}, "selected")


def test_notion_blocks_for_report():
    kinds = [b["type"] for b in notion.md_to_blocks(report.render(REPORT))]
    assert {"heading_2", "heading_3", "numbered_list_item", "bulleted_list_item", "divider"} <= set(kinds)


def test_notion_parent_env_then_config(tmp_path, monkeypatch):
    cfg = tmp_path / "publish.yaml"
    cfg.write_text("notion_parent_page_id: abc\n")
    monkeypatch.setattr(notion, "CONFIG", cfg)
    monkeypatch.delenv("NOTION_PARENT_PAGE_ID", raising=False)
    assert notion.parent() == "abc"
    monkeypatch.setenv("NOTION_PARENT_PAGE_ID", "env")
    assert notion.parent() == "env"


def test_daily_people_first(tmp_path, monkeypatch):
    def mk(id, title, src):
        return Episode(id=id, source_id=src, source_name="S", tier=1, title=title, url="u", published_at=NOW)

    order = []
    monkeypatch.setattr(fetch_mod, "discover", lambda *a, **k: order.append("channels") or
                        [mk("rss:c", "Jane Placeholder on robots", "widget"), mk("rss:d", "Other", "widget")])
    monkeypatch.setattr(fetch_mod, "discover_people", lambda people, now, days, known, n: order.append(("people", days)) or
                        ([mk("rss:p", "Jane Placeholder on robots", "person:Jane Placeholder")], {"per_source": {"itunes": 1}}))
    monkeypatch.setattr(fetch_mod, "load_people", lambda p: [])
    monkeypatch.setattr(fetch_mod, "get_transcript", lambda ep, budget: (None, None))
    args = SimpleNamespace(hours=36, sources="sources.yaml", only=None, db=str(tmp_path / "db"), runs=str(tmp_path), run_name="d",
                           per_source=3, people="people.yaml", per_person=3, whisper_minutes=None, whisper_per_episode=None,
                           refetch=False, no_transcribe=True, minutes=600, tmtb=None)
    run = fetch_mod.daily(args)
    m = json.loads((run / "manifest.json").read_text())
    assert order == [("people", 1.5), "channels"]
    assert [e["episode_id"] for e in m["episodes"]] == ["rss:p", "rss:d"] and m["days"] == 1.5


def test_notion_blocks_start_with_key_insights():
    blocks = notion.md_to_blocks(report.render(REPORT))
    assert blocks[0]["type"] == "heading_2" and blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Key Insights"


def test_report_without_token_writes_md_and_exits_zero(tmp_path, monkeypatch, capsys):
    from podcast_alpha.cli import main

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setattr(notion, "publish", lambda *a: pytest.fail("API called without token"))
    p = tmp_path / "report.json"
    p.write_text(json.dumps(REPORT))
    main(["report", str(p)])
    assert (tmp_path / "report.md").read_text().startswith("# Podcast Intel: test\n\n## Key Insights")
    assert "Notion connector" in capsys.readouterr().out


def test_fallback_text_flagged_and_queued(tmp_path):
    from podcast_alpha.fetch import refresh_flags, write_episode

    e = Episode(id="rss:x", source_id="person:Sam Altman", source_name="S", tier=2, title="Sam Altman on compute", url="u",
                published_at=NOW, audio_url="https://a.mp3")
    short = [{"start": 0, "speaker": None, "text": "show notes " * 150}]
    row = write_episode(tmp_path, e, short, "podcast:transcript")
    assert row["has_transcript"] is False and row["transcript_source"] == "fallback" and row["fallback_text"]
    assert row["fallback_from"] == "podcast:transcript"
    long = [{"start": i * 10, "speaker": None, "text": "word " * 100} for i in range(20)]
    assert write_episode(tmp_path, e, long, "podcast:transcript")["has_transcript"]
    old = {**write_episode(tmp_path, e, long, "podcast:transcript"), "file": write_episode(tmp_path, e, short, "x")["file"]}
    old.update(has_transcript=True, transcript_source="podcast:transcript")
    m = refresh_flags({"episodes": [old]}, tmp_path, 5000)
    assert m["episodes"][0]["transcript_source"] == "fallback" and not m["episodes"][0]["has_transcript"]


@pytest.mark.parametrize("title,desc,rerun", [
    ("Replay: What my kids taught me", "", True),
    ("Sarah Friar on AI", "This episode originally aired in May.", True),
    ("Best of 2026: chips", "", True),
    ("Jensen Huang on GPUs", "A new interview.", False),
])
def test_is_rerun(title, desc, rerun):
    from podcast_alpha.ingest import is_rerun
    assert is_rerun(title, desc) is rerun


def test_daily_excludes_out_of_window_and_reruns(tmp_path, monkeypatch):
    from datetime import timedelta

    def mk(id, title, age_h):
        return Episode(id=id, source_id="show", source_name="S", tier=1, title=title, url="u",
                       published_at=datetime.now(timezone.utc) - timedelta(hours=age_h))

    monkeypatch.setattr(fetch_mod, "discover", lambda *a, **k: [mk("rss:new", "Fresh", 3), mk("rss:old", "Old", 72),
                                                                 mk("rss:re", "Replay: classic", 5)])
    monkeypatch.setattr(fetch_mod, "get_transcript", lambda ep, budget: (None, None))
    args = SimpleNamespace(hours=48, sources="sources.yaml", only=None, db=str(tmp_path / "db"), runs=str(tmp_path), run_name="d",
                           per_source=3, people="", per_person=3, whisper_minutes=None, whisper_per_episode=None,
                           refetch=False, no_transcribe=True, minutes=600, tmtb=None)
    m = json.loads((fetch_mod.daily(args) / "manifest.json").read_text())
    assert [e["episode_id"] for e in m["episodes"]] == ["rss:new"]
    assert {x["episode_id"]: x["why"] for x in m["excluded"]} == {"rss:old": "outside window", "rss:re": "rerun"}


def test_selected_fallback_must_be_flagged():
    sel = {"date": "2026-10-21", "run_dir": "runs/x", "selected_by": "Cain",
           "episodes": [pick(0), pick(1), {**pick(2), "fallback_text": True}]}
    with pytest.raises(RankedError):
        report.validate(sel, "selected")
    sel["episodes"][2]["why"] = "Strong exec pick; fallback text only, whisper pending."
    report.validate(sel, "selected")


def test_report_our_read_optional_and_interests():
    d = copy.deepcopy(REPORT)
    d["episodes"][0]["signal"][0].pop("our_read")
    d["episodes"][0]["interests"] = "Early investor in Acme."
    md = report.render(report.validate(d, "report"))
    assert "- t [Jane Placeholder, [0:01:15](https://www.youtube.com/watch?v=a&t=75s)]\n" in md
    assert "**Interests to note:** Early investor in Acme." in md
