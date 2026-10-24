import json

from podcast_alpha import report
from podcast_alpha.cli import main


def cite(text, ts):
    return {"text": text, "speaker": "A", "timestamp": ts}


def make_report(eid="yt:1", youtube=True):
    ep = {"episode_id": eid, "headline": "Big Headline", "url": "https://x.test/1",
          "signal": [cite("Capex hits 450 billion", 600), cite("filler", 10), cite("filler", 20)],
          "sections": [{"title": "s", "body": "b", "claims": [cite("Margin of 38 percent", 900)]}]}
    if youtube:
        ep["youtube_url"] = "https://www.youtube.com/watch?v=abcdefghijk"
    return {"episodes": [ep], "essential": [{"episode_id": eid, "headline": "Big Headline"}]}


def make_run(tmp_path, transcript="[0:00:05] hello\n[0:20:00] capex is 450 billion\n[0:25:00] margin 38 now\n",
             with_file=True, with_manifest=True, eid="yt:1"):
    if with_manifest:
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"episodes": [{"episode_id": eid, "has_transcript": True, "file": "ep1.md"}]}))
    if with_file:
        (tmp_path / "ep1.md").write_text(f"# t\n\n## Transcript\n\n{transcript}")
    return tmp_path


def caps(*pairs):
    return lambda vid: [{"start": t, "speaker": None, "text": x} for t, x in pairs]


def test_warning_names_episode_position_number_and_nearest(tmp_path):
    run = make_run(tmp_path)
    ws = report.warn("report", make_report(), run)
    assert len(ws) == 2
    w = ws[0]
    assert 'yt:1 "Big Headline" signal[0]' in w
    assert "number 450" in w and "0:10:00 (ts 600s)" in w and "[transcript]" in w
    assert "nearest mention 0:20:00 (600s later)" in w and "move the timestamp" in w
    assert "sections[0].claims[0]" in ws[1] and "number 38" in ws[1]


def test_number_absent_everywhere_says_so(tmp_path):
    run = make_run(tmp_path, transcript="[0:00:05] hello\n[0:20:00] nothing\n")
    ws = report.warn("report", make_report(), run)
    assert all("not spoken anywhere in the transcript; fix or drop" in w for w in ws)


def test_dedupe_merges_transcript_and_captions_for_same_cite(tmp_path):
    run = make_run(tmp_path)
    t = report.warn("report", make_report(), run)
    c = report.caption_warn(make_report(), run, fetch=caps((5, "hi"), (1190, "capex is 450 billion"), (1500, "margin 38")))
    assert len(t) == 2 and len(c) == 2
    merged = report.dedupe_warnings(t + c)
    assert len(merged) == 2
    assert "[transcript, captions]" in merged[0]
    assert "transcript: nearest mention 0:20:00" in merged[0] and "captions: nearest mention 0:19:50" in merged[0]


def test_dedupe_collapses_repeats_and_keeps_distinct_and_order(tmp_path):
    run = make_run(tmp_path)
    t = report.warn("report", make_report(), run)
    assert report.dedupe_warnings(t + t) == t
    other = "report: yt:1: skipped: x"
    assert report.dedupe_warnings([other, t[0], other, t[1]]) == [other, t[0], t[1]]
    assert report.dedupe_warnings([]) == []


def test_dedupe_different_timestamp_or_number_not_merged(tmp_path):
    run = make_run(tmp_path)
    a = report.warn("report", make_report(), run)[0]
    b = a.replace("(ts 600s)", "(ts 601s)")
    c = a.replace("number 450", "number 451")
    assert len(report.dedupe_warnings([a, b, c])) == 3


def test_caption_fetch_failure_gives_one_skipped_line(tmp_path):
    def boom(vid):
        raise OSError("network down")

    ws = report.caption_warn(make_report(), make_run(tmp_path), fetch=boom)
    assert len(ws) == 1 and "skipped:" in ws[0] and "yt:1" in ws[0] and "OSError" in ws[0]


def test_caption_missing_or_malformed_captions_skip(tmp_path):
    run = make_run(tmp_path)
    ws = report.caption_warn(make_report(), run, fetch=lambda v: [])
    assert len(ws) == 1 and "skipped: no YouTube captions" in ws[0]
    ws = report.caption_warn(make_report(), run, fetch=lambda v: [{"nope": 1}])
    assert len(ws) == 1 and "skipped: unusable YouTube captions" in ws[0]
    assert report.caption_warn(make_report(youtube=False), run, fetch=lambda v: 1 / 0) == []


def test_caption_offset_line(tmp_path):
    rep = make_report()
    rep["episodes"][0]["signal"] = [cite("a 450 b", 700), cite("c 38 d", 800), cite("e 777 f", 900)]
    ws = report.caption_warn(rep, make_run(tmp_path), fetch=caps((100, "450"), (200, "38"), (300, "777")))
    assert any("cite timestamps run 600s later" in w and 'yt:1 "Big Headline"' in w for w in ws)


def test_check_cli_prints_one_line_per_cite(tmp_path, capsys, monkeypatch):
    run = make_run(tmp_path)
    from podcast_alpha import transcribe

    monkeypatch.setattr(transcribe, "from_youtube", caps((5, "hi"), (1190, "capex is 450 billion"), (1500, "margin 38")))
    rep = make_report()
    rep.update(date="2026-10-23", title="t", run_dir="x", key_insights=["a", "b", "c"])
    monkeypatch.setattr(report, "validate", lambda d, k: d)
    monkeypatch.setattr(report, "verify", lambda k, d, r: [])
    (run / "report.json").write_text(json.dumps(rep))
    main(["check", "report", str(run / "report.json"), "--captions"])
    out = [l for l in capsys.readouterr().out.splitlines() if "warning:" in l]
    assert len(out) == 2 and all("[transcript, captions]" in l for l in out)


def test_warn_transcript_file_missing_is_one_skip_line(tmp_path):
    run = make_run(tmp_path, with_file=False)
    ws = report.warn("report", make_report(), run)
    assert len(ws) == 1 and "number check skipped" in ws[0]


# ranked_verify
def ranked(claims, eid="yt:1"):
    return {"episodes": [{"episode_id": eid, "title": "Ep One", "claims": claims}]}


RC = {"claim": "capex 450 billion", "timestamp": 600, "speaker": "A", "ticker": [], "novelty": 1, "verifiable": 1}


def test_ranked_verify_no_manifest_warns_once(tmp_path):
    errs, warns = report.ranked_verify(ranked([RC]), tmp_path)
    assert errs == [] and len(warns) == 1 and "no manifest.json" in warns[0]


def test_ranked_verify_corrupt_manifest_warns_once(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json")
    errs, warns = report.ranked_verify(ranked([RC]), tmp_path)
    assert errs == [] and len(warns) == 1 and "unreadable" in warns[0]


def test_ranked_verify_missing_transcript_file_warns_not_crashes(tmp_path):
    run = make_run(tmp_path, with_file=False)
    errs, warns = report.ranked_verify(ranked([RC]), run)
    assert errs == [] and len(warns) == 1 and "missing" in warns[0] and "yt:1" in warns[0]
    assert report.ranked_verify(ranked([]), run) == ([], [])


def test_ranked_verify_number_warning_uses_title_and_claims_position(tmp_path):
    run = make_run(tmp_path)
    errs, warns = report.ranked_verify(ranked([RC]), run)
    assert errs == [] and len(warns) == 1
    assert warns[0].startswith('ranked: yt:1 "Ep One" claims[0]: number 450')
