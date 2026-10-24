import io
import json
from pathlib import Path

import jsonschema
import pytest

from podcast_alpha import library
from podcast_alpha.cli import main

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" / "library-claim.schema.json").read_text())


def validator(defn=None):
    s = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], **({"$ref": f"#/$defs/{defn}"} if defn else
                                                                  {"type": "array", "items": {"$ref": "#/$defs/claim"}})}
    return jsonschema.Draft202012Validator(s)


TRANSCRIPT = """---\nepisode_id: ep1\n---\n\n# T\n\n## Transcript\n\n""" + "\n".join(
    f"[0:{m:02d}:{s:02d}] " + ("We are betting on hybrid bonding for HBM stacks" if (m, s) == (1, 10) else f"filler talk number {m}{s}")
    for m in range(0, 3) for s in range(0, 60, 10)) + "\n"


def make_run(root: Path, name="2026-10-20", claim="Nvidia HBM supply is sold out through 2027.") -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "ep1.md").write_text(TRANSCRIPT)
    (run / "manifest.json").write_text(json.dumps({"run_at": "2026-10-20T10:00:00", "episodes": [
        {"episode_id": "ep1", "file": "ep1.md", "title": "Jensen Huang on HBM", "source": "Show A", "tier": 1,
         "published": "2026-10-19", "url": "https://a.example/ep1", "youtube_id": "abc123", "has_transcript": True,
         "fallback_text": False, "transcript_source": "podcast:transcript", "transcript_url": "https://a.example/ep1.vtt"},
        {"episode_id": "ep2", "file": "ep2.md", "title": "No transcript", "source": "Show B", "tier": 2,
         "published": "2026-10-10", "url": "https://b.example/ep2", "has_transcript": False}]}))
    (run / "ranked.json").write_text(json.dumps({"week_of": "2026-10-19", "run_dir": str(run), "ranked_by": "t", "episodes": [
        {"rank": 1, "episode_id": "ep1", "title": "Jensen Huang on HBM", "url": "https://a.example/ep1", "source": "Show A",
         "tier": 1, "published": "2026-10-19", "score": 8, "reason": "r",
         "claims": [{"ticker": ["nvda", "private:Foo"], "claim": claim, "speaker": "Jensen Huang", "timestamp": 70,
                     "novelty": 2, "verifiable": 3}]},
        {"rank": 2, "episode_id": "ep2", "title": "No transcript", "url": "https://b.example/ep2", "source": "Show B",
         "tier": 2, "published": "2026-10-10", "score": 3, "reason": "r",
         "claims": [{"ticker": ["AMZN"], "claim": "Amazon Trainium demand is rising.", "speaker": "Andy Jassy", "timestamp": 5,
                     "novelty": 1, "verifiable": 1}]}]}))
    (run / "report.json").write_text(json.dumps({"date": "2026-10-20", "title": "t", "run_dir": str(run), "episodes": [
        {"episode_id": "ep1", "headline": "Jensen on HBM", "url": "https://a.example/ep1",
         "youtube_url": "https://www.youtube.com/watch?v=abc123",
         "signal": [{"text": "Oracle capex doubles next year.", "speaker": "Jensen Huang", "timestamp": 30}],
         "sections": [{"title": "s", "body": "b", "claims": [{"text": "Oracle capex doubles next year.", "speaker": "Jensen Huang", "timestamp": 30},
                                                              {"text": "Unknown timing remark.", "speaker": "unknown", "timestamp": None}]}]}]}))
    return run


@pytest.fixture
def db(tmp_path):
    run = make_run(tmp_path / "runs")
    path = tmp_path / "lib.sqlite"
    library.ingest_run(run, path)
    return path


def test_ingest_counts_and_counts_stable_on_reingest(tmp_path):
    run = make_run(tmp_path / "runs")
    path = tmp_path / "lib.sqlite"
    first = library.ingest_run(run, path)
    assert first["episodes"] == 2 and first["claims"] == 4 and first["chunks"] >= 2
    before = library.stats(path)
    assert library.ingest_run(run, path) == first
    after = library.stats(path)
    for k in ("episodes", "claims", "transcript_chunks", "claims_by_source"):
        assert before[k] == after[k]


def test_reingest_replaces_changed_claims(tmp_path):
    run = make_run(tmp_path / "runs")
    path = tmp_path / "lib.sqlite"
    library.ingest_run(run, path)
    ranked = json.loads((run / "ranked.json").read_text())
    ranked["episodes"][0]["claims"][0]["claim"] = "Rubin ramps in Q3."
    (run / "ranked.json").write_text(json.dumps(ranked))
    library.ingest_run(run, path)
    assert library.query_claims(path, text="Rubin") and not library.query_claims(path, text="sold out")


def test_ingest_does_not_write_into_run(tmp_path):
    run = make_run(tmp_path / "runs")
    before = {p.name: p.stat().st_mtime_ns for p in run.iterdir()}
    library.ingest_run(run, tmp_path / "elsewhere" / "lib.sqlite")
    assert {p.name: p.stat().st_mtime_ns for p in run.iterdir()} == before


def test_query_filters(db):
    nv = library.query_claims(db, ticker="NVDA")
    assert [c["speaker"] for c in nv] == ["Jensen Huang"] and nv[0]["tickers"] == ["NVDA", "private:Foo"]
    assert library.query_claims(db, ticker="nvda")  # case-insensitive
    assert [c["text"] for c in library.query_claims(db, ticker="private:foo")] == [nv[0]["text"]]
    assert len(library.query_claims(db, person="Jassy")) == 1
    assert {c["episode_id"] for c in library.query_claims(db, person="Jensen")} == {"ep1"}  # speaker or title
    assert {c["episode_id"] for c in library.query_claims(db, since="2026-10-15")} == {"ep1"}
    assert {c["episode_id"] for c in library.query_claims(db, until="2026-10-15")} == {"ep2"}
    assert len(library.query_claims(db, limit=2)) == 2
    assert library.query_claims(db, text="oracle")[0]["source"] == "report"
    assert library.query_claims(db, ticker="ORCL") == []


def add_untagged(db, *texts):
    con = library.connect(db, write=True)
    with con:
        for i, t in enumerate(texts):
            con.execute("INSERT INTO claims(episode_id, run, run_date, date, source, text) VALUES ('ep1','x','2026-10-20','2026-10-20','report',?)", (t,))
    con.close()


def test_untagged_claim_ticker_text_match_is_strict(db):
    add_untagged(db, "Right now demand is strong.", "Input costs fell.", "AI spend is up; NOW is a winner.",
                 "Bought $NOW and $AI.", "Costco beat. COST guidance is raised.")
    texts = lambda **kw: sorted(c["text"] for c in library.query_claims(db, limit=0, **kw))  # noqa: E731
    assert "Right now demand is strong." not in texts(ticker="NOW")
    assert "Input costs fell." not in texts(ticker="COST")
    assert texts(ticker="COST") == ["Costco beat. COST guidance is raised."]
    assert texts(ticker="AI") == []  # 2-char symbols never match bare text
    assert texts(ticker="$AI") == ["Bought $NOW and $AI."]
    assert texts(ticker="$NOW") == ["Bought $NOW and $AI."]
    assert texts(ticker="NOW") == ["AI spend is up; NOW is a winner."]  # bare NOW does not match $NOW


def test_ticker_tag_and_matched_by(db):
    rows = library.query_claims(db, ticker="nvda")
    assert [(r["source"], r["matched_by"]) for r in rows] == [("ranked", "tag")]
    add_untagged(db, "NVDA and AMD both rallied.")
    got = {r["text"]: r["matched_by"] for r in library.query_claims(db, ticker="NVDA", limit=0)}
    assert got["NVDA and AMD both rallied."] == "text"
    assert library.query_claims(db, ticker="Oracle") == []  # company names are not tickers
    assert library.query_claims(db)[0]["matched_by"] is None
    validator().validate(library.query_claims(db, ticker="NVDA", limit=0))


def test_links_and_metadata(db):
    c = library.query_claims(db, text="HBM")[0]
    assert c["link"] == "https://www.youtube.com/watch?v=abc123&t=70s"
    assert c["transcript_url"] == "https://a.example/ep1.vtt" and c["transcript_path"].endswith("ep1.md")
    assert library.query_claims(db, person="Jassy")[0]["link"] == "https://b.example/ep2"


def test_transcript_search(db):
    hits = library.search_transcripts(db, "hybrid bonding")
    assert len(hits) == 1 and hits[0]["episode_id"] == "ep1" and "**hybrid**" in hits[0]["snippet"]
    assert hits[0]["timestamp"] == 60 and hits[0]["link"].endswith("&t=60s")
    assert library.search_transcripts(db, '"bonding hybrid"') == []
    assert library.search_transcripts(db, "hybrid", since="2026-10-25") == []
    with pytest.raises(ValueError):
        library.search_transcripts(db, "!!!")


def test_fts_syntax_is_safe(db):
    assert library.query_claims(db, text='AND OR "unterminated NEAR(') == []


def test_export_and_schema(db, tmp_path):
    buf = io.StringIO()
    assert library.export_jsonl(db, buf, since="2026-10-15") == 3
    rows = [json.loads(line) for line in buf.getvalue().splitlines()]
    validator().validate(rows)
    validator().validate(library.query_claims(db))
    for h in library.search_transcripts(db, "filler"):
        validator("transcript_hit").validate(h)


def test_cli_roundtrip(tmp_path, capsys):
    root = tmp_path / "runs"
    make_run(root, "a")
    make_run(root, "b", claim="Different claim.")
    path = str(tmp_path / "x.sqlite")
    main(["library", "ingest", "--all", "--runs", str(root), "--db", path])
    capsys.readouterr()
    main(["library", "query", "--ticker", "AMZN", "--json", "--db", path])
    out = capsys.readouterr().out
    rows = json.loads(out)
    validator().validate(rows)
    assert len(rows) == 2
    main(["library", "stats", "--db", path])
    assert json.loads(capsys.readouterr().out)["runs"] == ["a", "b"]
    out_file = tmp_path / "feed.jsonl"
    main(["library", "export", "--out", str(out_file), "--db", path])
    assert len(out_file.read_text().splitlines()) == 8


def test_cross_run_preserves_transcript_and_links(tmp_path):
    root = tmp_path / "runs"
    a = make_run(root, "2026-10-20")
    path = tmp_path / "lib.sqlite"
    library.ingest_run(a, path)
    # run W lists ep1 without a transcript, link, tier or score
    w = root / "2026-10-27"
    w.mkdir()
    (w / "manifest.json").write_text(json.dumps({"run_at": "2026-10-27T10:00:00", "episodes": [
        {"episode_id": "ep1", "file": "ep1.md", "title": "Jensen Huang on HBM", "source": "Show A",
         "has_transcript": False}]}))
    r = library.ingest_run(w, path)
    assert r["chunks"] == 0
    assert library.search_transcripts(path, '"hybrid bonding"')
    con = library.connect(path)
    row = con.execute("SELECT * FROM episodes WHERE episode_id='ep1'").fetchone()
    con.close()
    assert row["has_transcript"] == 1 and row["transcript_path"].endswith("ep1.md")
    assert row["youtube_url"].endswith("abc123") and row["url"] == "https://a.example/ep1"
    assert row["tier"] == 1 and row["score"] == 8
    before = library.stats(path)
    library.ingest_run(w, path)  # same-run re-ingest stays idempotent and still preserves
    library.ingest_run(a, path)
    after = library.stats(path)
    assert before["transcript_chunks"] == after["transcript_chunks"] and before["episodes"] == after["episodes"]
    assert library.query_claims(path, text="HBM")[0]["youtube_url"].endswith("abc123")


def test_read_while_ingest_holds_write_lock(db):
    import sqlite3

    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO claims(episode_id, run, source, text) VALUES ('ep1','y','report','pending')")
    try:
        assert library.query_claims(db, text="HBM")
        assert library.stats(db)["claims"] >= 3
        assert library.export_jsonl(db, io.StringIO()) >= 3
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_missing_db_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="library ingest"):
        library.query_claims(tmp_path / "nope.sqlite")
    assert not (tmp_path / "nope.sqlite").exists()
