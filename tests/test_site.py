import json

from podcast_alpha import site

EP = "rss:aaa"


def make_published(root):
    d = root / "2026-01-05-weekly"
    d.mkdir(parents=True)
    claim = {"ticker": ["NVDA", "private:Acme"], "claim": "Chips <b>sold</b> out", "speaker": "Jane <i>Doe</i>",
             "timestamp": 3725, "novelty": 2, "verifiable": 1}
    ranked = {"week_of": "2026-01-05", "notes": "2 fetched", "episodes": [
        {"episode_id": EP, "title": "<script>alert(1)</script> Boss", "url": "https://example.com/e", "source": "Show A",
         "tier": 1, "published": "2026-01-04", "score": 8, "reason": "Because <em>x</em>", "claims": [claim], "rank": 1}]}
    (d / "ranked.json").write_text(json.dumps(ranked))
    (d / "episodes.json").write_text(json.dumps([
        {"episode_id": EP, "title": "t", "source": "Show A", "has_transcript": True},
        {"episode_id": "rss:bbb", "title": "u", "source": "Show B", "has_transcript": False}]))
    rows = [{"episode_id": EP, "title": "<script>alert(1)</script> Boss", "show": "Show A", "published": "2026-01-04",
             "date": "2026-01-04", "tier": 1, "speaker": "Jane <i>Doe</i>", "timestamp": 3725, "text": "Chips <b>sold</b> out",
             "tickers": ["NVDA", "private:Acme"], "novelty": 2, "verifiable": 1, "source": "ranked", "run": d.name, "score": 8,
             "url": "https://example.com/e", "youtube_url": None, "transcript_url": "https://example.com/t",
             "link": "https://example.com/e?t=3725"},
            {**{}, "episode_id": EP, "title": "x", "show": "Show A", "speaker": "Bob", "timestamp": 5, "text": "No tickers",
             "tickers": ["AMD"], "run": d.name, "link": "javascript:alert(1)"}]
    (d / "claims.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_build(tmp_path):
    pub = tmp_path / "published"
    make_published(pub)
    out = tmp_path / "dist"
    site.build(pub, out)
    for f in ["index.html", "about.html", "style.css", "claims.jsonl", "runs.json", "tickers/index.html", "tickers/NVDA.html",
              "tickers/AMD.html", "runs/2026-01-05-weekly/index.html", "runs/2026-01-05-weekly/claims.jsonl"]:
        assert (out / f).exists(), f
    assert not (out / "tickers" / "Acme.html").exists()
    assert "Acme" in (out / "tickers" / "index.html").read_text()

    run = (out / "runs/2026-01-05-weekly/index.html").read_text()
    assert "<script>alert" not in run and "&lt;script&gt;alert(1)&lt;/script&gt;" in run
    assert "<b>sold</b>" not in run and "1:02:05" in run and "Transcript" in run
    assert "javascript:" not in (out / "tickers/AMD.html").read_text()

    nv = (out / "tickers/NVDA.html").read_text()
    assert "Chips" in nv and "No tickers" not in nv
    assert "No tickers" in (out / "tickers/AMD.html").read_text()

    idx = json.loads((out / "runs.json").read_text())
    assert idx["runs"][0] == {"run": "2026-01-05-weekly", "kind": "weekly", "date": "2026-01-05", "episodes": 1, "claims": 2,
                              "page": "/runs/2026-01-05-weekly/", "claims_path": "/runs/2026-01-05-weekly/claims.jsonl"}
    assert len((out / "claims.jsonl").read_text().splitlines()) == 2


def test_empty_published(tmp_path):
    site.build(tmp_path / "none", tmp_path / "dist")
    assert "No runs published" in (tmp_path / "dist" / "index.html").read_text()
