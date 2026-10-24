import json

from podcast_alpha import ranked, rank

TS = "\n".join(f"[0:{m:02d}:{s:02d}] words about topic number {m} {s}" for m in range(0, 30) for s in (0, 10, 20, 30, 40, 50))


def episode_md(title: str, body: str = TS) -> str:
    return f"---\ntitle: {title}\n---\n\n# {title}\n\n## Description\n\nd\n\n## Transcript\n\n{body}\n"


def make_run(tmp_path):
    rows = []
    for i, (title, tier, body) in enumerate([("Alpha", 1, TS), ("Beta", 2, TS), ("Gamma", 1, TS.replace("topic", "subject"))]):
        name = f"2026-10-2{i}-{title.lower()}.md"
        (tmp_path / name).write_text(episode_md(title, body))
        rows.append({"episode_id": f"rss:{i}", "file": name, "title": title, "source": f"Show {title}", "tier": tier,
                     "published": f"2026-10-2{i}", "url": f"https://x/{i}", "has_transcript": True, "fallback_text": False,
                     "transcript_chars": 1000 - i, "transcript_minutes": 10, "transcript_source": "podcast:transcript"})
    rows.append({"episode_id": "rss:9", "file": "none.md", "title": "NoText", "source": "S", "tier": 1, "published": "2026-10-19",
                 "url": "u", "has_transcript": False})
    (tmp_path / "manifest.json").write_text(json.dumps({"episodes": rows}))
    return rows


def test_condense_keeps_timestamps_every_step():
    out = rank.condense(episode_md("T"), step=30)
    body = out.partition("## Transcript")[2]
    assert body.count("[0:00:00]") == 1 and "[0:00:30]" in body and "[0:00:10]" not in body
    assert len(out) < len(episode_md("T"))


def test_prep_batches_only_real_transcripts(tmp_path):
    make_run(tmp_path)
    info = rank.prep(tmp_path, batches=2)
    assert info == {"transcribed": 3, "without_transcript": 1, "batches": 2}
    ids = [e["episode_id"] for k in (1, 2) for e in json.loads((tmp_path / "rank" / f"batch-{k}-in.json").read_text())]
    assert sorted(ids) == ["rss:0", "rss:1", "rss:2"]
    assert (tmp_path / "rank" / "cond" / "2026-10-20-alpha.md").exists()


def reader_row(r, score, **kw):
    return {"episode_id": r["episode_id"], "title": r["title"], "url": r["url"], "source": r["source"], "tier": r["tier"],
            "published": r["published"], "score": score, "reason": "why", "claims": [], **kw}


def test_merge_orders_dedupes_and_validates(tmp_path):
    rows = make_run(tmp_path)
    (tmp_path / "rank").mkdir()
    (tmp_path / "rank" / "batch-1-out.json").write_text(json.dumps([reader_row(rows[0], 6), reader_row(rows[1], 8)]))
    (tmp_path / "rank" / "batch-2-out.json").write_text(json.dumps([reader_row(rows[2], 8), reader_row(rows[0], 6)]))
    data = rank.merge(tmp_path, "tester")
    assert [e["title"] for e in data["episodes"]] == ["Gamma", "Beta"]  # Beta repeats Alpha's transcript; its 8 beats Alpha's 6, then tier orders the 8s
    assert [e["rank"] for e in data["episodes"]] == [1, 2]
    assert "1 had no usable transcript" in data["notes"] and "duplicates merged: Alpha" in data["notes"]
    ranked.validate(data)


def test_merge_rejects_unknown_ids(tmp_path):
    rows = make_run(tmp_path)
    (tmp_path / "rank").mkdir()
    bad = dict(reader_row(rows[0], 5), episode_id="rss:nope")
    (tmp_path / "rank" / "batch-1-out.json").write_text(json.dumps([bad]))
    try:
        rank.merge(tmp_path, "tester")
    except ValueError as exc:
        assert "rss:nope" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_merge_does_not_collapse_episodes_that_share_an_intro(tmp_path):
    rows = make_run(tmp_path)
    ad = "this episode is brought to you by acme corp " * 60
    for i in (0, 1):
        body = ad + "\n".join(f"[0:{m:02d}:00] unique talk {i} segment {m} " + "x" * 200 for m in range(30))
        (tmp_path / rows[i]["file"]).write_text(episode_md(rows[i]["title"], body))
    (tmp_path / "rank").mkdir()
    (tmp_path / "rank" / "batch-1-out.json").write_text(json.dumps([reader_row(rows[0], 6), reader_row(rows[1], 7)]))
    assert len(rank.merge(tmp_path, "t")["episodes"]) == 2


def test_merge_enforces_tier3_cap_and_top3(tmp_path):
    rows = make_run(tmp_path)
    (tmp_path / rows[1]["file"]).write_text(episode_md("Beta", TS.replace("topic", "matter")))  # not Alpha's twin here
    t3 = [dict(reader_row(rows[i % 3], 9 - i), episode_id=f"rss:t{i}", title=f"T3 {i}", tier=3) for i in range(3)]
    man = json.loads((tmp_path / "manifest.json").read_text())
    for r in t3:
        man["episodes"].append({"episode_id": r["episode_id"], "file": "none.md", "title": r["title"], "source": "S", "tier": 3,
                                "published": "2026-10-20", "url": "u", "has_transcript": True})
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    (tmp_path / "rank").mkdir()
    base = [reader_row(rows[0], 5), reader_row(rows[1], 5), reader_row(rows[2], 4)]
    (tmp_path / "rank" / "batch-1-out.json").write_text(json.dumps(t3 + base))
    data = rank.merge(tmp_path, "t")
    ranked.validate(data)  # tier-3 rules hold, so the merge output is publishable
    assert sum(e["tier"] == 3 for e in data["episodes"]) == 2
    assert all(e["tier"] != 3 for e in data["episodes"][:3])
