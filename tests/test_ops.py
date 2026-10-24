import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from podcast_alpha import fetch as fetch_mod
from podcast_alpha import timing, tmtb, whisper
from podcast_alpha.cli import main
from podcast_alpha.people import Person
from podcast_alpha.triage import triage
from tests.test_runtime import LONG, ep, make_run, targs

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
EMAIL = (Path(__file__).parent / "fixtures" / "reference_email.txt").read_text()
PEOPLE = [Person("Jane Placeholder", "CEO", "core"), Person("Omar Example", "founder", "watch")]


def row(i, title, tag="", tier=2, news=False, audio=True, has=False, published="2026-10-22"):
    return {"episode_id": i, "title": title, "source": "Show", "source_tag": tag, "tier": tier, "news": news,
            "audio_url": "https://cdn.example/a.mp3" if audio else None, "has_transcript": has, "published": published}


def test_timing_apply_and_e2e():
    m = {}
    timing.apply(m, "fetch", "start", "2026-10-22T11:00:00+00:00")
    timing.apply(m, "fetch", "end", "2026-10-22T11:05:00+00:00")
    timing.apply(m, "publish", "start", "2026-10-22T16:00:00+00:00")
    timing.apply(m, "publish", "end", "2026-10-22T16:01:30+00:00")
    assert m["timing"]["fetch"]["seconds"] == 300 and m["timing"]["publish"]["seconds"] == 90
    assert m["timing"]["e2e"]["seconds"] == 5 * 3600 + 90


def test_mark_and_stats_cli(tmp_path, capsys):
    run = tmp_path / "2026-10-22"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"episodes": [row("a", "x", has=True), row("b", "y")],
                                                   "recall": {"tmtb": 3, "hits": 2, "recall": 0.667}}))
    main(["mark", "select", "start", "--run", str(run)])
    main(["mark", "select", "end", "--run", str(run)])
    m = json.loads((run / "manifest.json").read_text())
    assert m["timing"]["select"]["seconds"] >= 0 and "e2e" in m["timing"]
    main(["stats", "--runs", str(tmp_path)])
    out = capsys.readouterr().out
    assert "2026-10-22" in out and "1/2" in out and "2/3 (67%)" in out and "active" in out and "idle" in out and "wall" in out


def test_triage_deterministic_top_n_keeps_core():
    m = {"episodes": [row("n1", "Squawk Pod: markets", tier=1, news=True), row("t1", "Founder interview on AI chips", tier=1),
                      row("t2", "Random chat", tier=3), row("t3", "CFO on guidance", tier=2),
                      row("core", "Old show", tag="person:Jane Placeholder", tier=3, published="2026-10-10"),
                      row("done", "Already done", has=True), row("w", "Omar Example on rates", tier=3)]}
    ids = triage(m, PEOPLE, top=2, now=NOW, is_news=lambda e: e["news"])
    assert ids == triage(json.loads(json.dumps(m)), PEOPLE, top=2, now=NOW, is_news=lambda e: e["news"])
    assert "core" in ids and len(ids) == 2 + (0 if "core" in ids[:2] else 1)
    e = {x["episode_id"]: x for x in m["episodes"]}
    assert e["n1"]["triage"]["whisper"] is False and "news show" in e["n1"]["triage"]["reasons"]
    assert "rank" not in e["done"]["triage"] and m["triage"]["candidates"] == 6
    ids1 = triage(m, PEOPLE, top=1, now=NOW, is_news=lambda e: e["news"])
    assert "core" in ids1  # core person never dropped


def test_transcribe_whispers_only_triaged(monkeypatch, tmp_path):
    eps = [ep("rss:a", "Founder interview on AI chips"), ep("rss:b", "Random chat"), ep("rss:c", "CEO on guidance")]
    run = make_run(tmp_path / "r", [replace(e, tier=2) for e in eps])  # tier 2: the tier-1 floor would whisper all three
    monkeypatch.setattr(whisper, "available", lambda: True)
    calls = []
    monkeypatch.setattr(whisper, "transcribe", lambda url, b: calls.append(url) or (LONG, 180.0))
    fetch_mod.transcribe_run(targs(run, top=2, people=None))
    m = json.loads((run / "manifest.json").read_text())
    assert len(calls) == 2 and "https://cdn.example/rss:b.mp3" not in calls
    assert m["triage"] == {"top": 2, "tier1_floor": True, "candidates": 3, "whisper": 2, "clips": {}}
    assert m["timing"]["transcribe"]["seconds"] >= 0


def test_tmtb_writes_recall(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"episodes": [{"episode_id": "rss:1", "title": "Jane Placeholder on robots", "source": "Widget"}]}))
    (run / "selected.json").write_text(json.dumps({"episodes": [
        {"episode_id": "rss:1", "title": "Jane Placeholder on robots", "show": "The Widget Show", "guests": [{"name": "Jane Placeholder"}]},
        {"episode_id": "rss:2", "title": "Something else", "show": "X", "guests": [{"name": "Zed Nobody"}]}]}))
    tmtb.run(EMAIL, run, fetch_json=lambda t: [], now=NOW)
    rec = json.loads((run / "manifest.json").read_text())["recall"]
    assert rec["tmtb"] == 3 and rec["hits"] == 1 and rec["recall"] == 0.333 and rec["picks"] == 2
    assert len(rec["missed"]) == 2 and rec["found_in_run"] == 1


def test_triage_scores_headline_guest_titles():
    titles = {"pair": "Arvind Krishna & Nikesh Arora: Quantum Threat, Open vs Frontier AI, AI Cyber Warfare & SaaS Collapse",
              "ceo": "Amazon CEO Andy Jassy on the $220 Billion AI Buildout",
              "on": "Sundar Pichai on Gemini 4 and the AI compute crunch",
              "role": "Intel CTO Sachin Katti Talks Foundry", "comma": "Jane Smith, CEO of AMD",
              "trail": "Datacenters | Jensen Huang",
              "none": "Weekly Market Recap", "ep": "Ep. 039 - Datacenters", "trade": "The AI Trade Cracks",
              "colon": "Weekly Market: Stocks Fall", "mode": "Founder Mode Explained"}
    m = {"episodes": [row(k, t, tier=1) for k, t in titles.items()]}
    triage(m, [], top=3, now=NOW)
    e = {x["episode_id"]: x["triage"] for x in m["episodes"]}
    assert any(r.startswith("two named guests (Arvind Krishna, Nikesh Arora)") for r in e["pair"]["reasons"])
    assert e["pair"]["score"] > e["none"]["score"] + 40
    for k in ("ceo", "on", "role", "comma", "trail"):
        assert "named guest" in e[k]["reasons"], k
    for k in ("none", "ep", "trade", "colon", "mode"):
        assert not any("named guest" in r for r in e[k]["reasons"]), k
    assert e["pair"]["score"] > e["on"]["score"] > e["none"]["score"]  # pair > single guest > no guest


def test_named_guests_ignores_show_names_and_company_pairs():
    from podcast_alpha.triage import named_guests

    assert named_guests("Why Rates Fell | Odd Lots") == [] and named_guests("Why Rates Fell | Planet Money") == []
    assert named_guests("Why Rates Fell | Ada Quill") == ["Ada Quill"]  # a real trailing guest still counts...
    assert named_guests("Why Rates Fell | Ada Quill", "Ada Quill Radio") == []  # ...unless it is the show's own name
    assert named_guests("Morgan Stanley & Goldman Sachs: Who Wins the IPO Season") == []
    assert named_guests("Bitcoin Miners and Data Centers: A Merger Wave") == []
    assert named_guests("Arvind Krishna & Nikesh Arora: Quantum Threat") == ["Arvind Krishna", "Nikesh Arora"]
