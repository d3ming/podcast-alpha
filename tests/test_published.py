import json

import pytest

from podcast_alpha import published


def _run(tmp_path):
    run = tmp_path / "runs" / "2026-10-20"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"episodes": [
        {"episode_id": "rss:a", "title": "A", "source": "Show", "published": "2026-10-20", "tier": 1, "file": "a.md",
         "has_transcript": True, "description": "show notes copied from the feed", "audio_url": "https://x/a.mp3"}]}))
    (run / "a.md").write_text("[0:00:01] full transcript text\n")
    (run / "missed.md").write_text("from the reference newsletter")
    (run / "ranked.json").write_text(json.dumps({"week_of": "2026-10-20", "episodes": [
        {"episode_id": "rss:a", "title": "A", "source": "Show", "tier": 1, "published": "2026-10-20", "score": 7,
         "claims": [{"claim": "Capex rises", "speaker": "CEO", "timestamp": 61, "ticker": ["nvda"]}]}]}))
    (run / "digest-2026-10-20.md").write_text("# digest\n")
    return run


def test_export_keeps_outputs_and_drops_transcripts_and_newsletter(tmp_path):
    dest, files = published.export(_run(tmp_path), root=tmp_path / "published")
    assert sorted(p.name for p in dest.iterdir()) == sorted(files) == [
        "claims.jsonl", "digest-2026-10-20.md", "episodes.json", "ranked.json"]
    ep = json.loads((dest / "episodes.json").read_text())[0]
    assert "description" not in ep and "audio_url" not in ep and ep["title"] == "A"
    claim = json.loads((dest / "claims.jsonl").read_text().splitlines()[0])
    assert claim["text"] == "Capex rises" and claim["tickers"] == ["NVDA"] and "transcript_path" not in claim


def test_export_replaces_previous_and_refuses_empty_run(tmp_path):
    run = _run(tmp_path)
    dest, _ = published.export(run, root=tmp_path / "published")
    (dest / "stale.txt").write_text("old")
    published.export(run, root=tmp_path / "published")
    assert not (dest / "stale.txt").exists()
    empty = tmp_path / "runs" / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        published.export(empty, root=tmp_path / "published")
