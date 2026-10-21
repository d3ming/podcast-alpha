import copy
import json

import pytest

from podcast_alpha import ranked
from podcast_alpha.cli import main
from podcast_alpha.digest import render
from podcast_alpha.fetch import episode_md, slug
from podcast_alpha.ingest import Episode
from podcast_alpha.notion import md_to_blocks, rich_text


def ep(rank, tier=1, score=5, claims=None):
    return {"rank": rank, "episode_id": f"yt:{rank}", "title": f"Ep {rank}", "url": f"https://www.youtube.com/watch?v={rank}",
            "source": "S", "tier": tier, "published": "2026-10-19", "score": score, "reason": "why",
            "claims": claims or []}


CLAIM = {"ticker": ["NVDA"], "claim": "c", "speaker": "unknown", "timestamp": 61, "novelty": 2, "verifiable": 3}
GOOD = {"week_of": "2026-10-14", "run_dir": "runs/2026-10-21", "ranked_by": "Cain",
        "episodes": [ep(1, score=9, claims=[CLAIM]), ep(2, score=7), ep(3, score=5), ep(4, tier=3, score=4), ep(5, score=2)]}


def test_valid_ranked_passes_without_warnings():
    assert ranked.validate(copy.deepcopy(GOOD)) == []


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["episodes"][0].update(score=11), "schema"),
    (lambda d: d["episodes"][0]["claims"][0].update(novelty=4), "schema"),
    (lambda d: d["episodes"][0].update(weight=0.1), "schema"),
    (lambda d: d.update(notes="holdings: x", extra=1), "schema"),
    (lambda d: d["episodes"][1].update(rank=5), "ranks"),
    (lambda d: d["episodes"][2].update(tier=3), "top pick"),
    (lambda d: [d["episodes"][i].update(tier=3) for i in (3, 4)] and d["episodes"][3].update(rank=4)
     or d["episodes"].append(ep(6, tier=3)), "cap"),
])
def test_invalid_ranked_rejected(mutate, msg):
    d = copy.deepcopy(GOOD)
    mutate(d)
    with pytest.raises(ranked.RankedError, match=msg):
        ranked.validate(d)


def test_holdings_words_rejected_anywhere():
    d = copy.deepcopy(GOOD)
    d["episodes"][0]["claims"][0]["ticker"] = ["NVDA"]
    d["notes"] = "fine"
    ranked.validate(d)
    d["episodes"][0]["reason"] = 'x"holdings'
    with pytest.raises(ranked.RankedError, match="holdings"):
        ranked.validate(d)


def test_clustered_scores_warn():
    d = copy.deepcopy(GOOD)
    for e in d["episodes"]:
        e["score"] = 9 if e["tier"] == 1 else 8
    assert ranked.validate(d)


def test_render_and_notion_blocks():
    md = render(GOOD)
    assert "Week of 2026-10-14" in md and "t=61s" in md and "Ranked by Cain" in md
    blocks = md_to_blocks(md)
    assert any(b["type"] == "table" for b in blocks)
    assert any(b["type"] == "bulleted_list_item" for b in blocks)
    rt = rich_text("see [x](https://a) **b**")
    assert rt[1]["text"]["link"]["url"] == "https://a" and rt[3]["annotations"]["bold"]


def test_publish_cli_writes_markdown(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    p = tmp_path / "ranked.json"
    p.write_text(json.dumps(GOOD))
    main(["publish", str(p)])
    assert (tmp_path / "digest-2026-10-14.md").exists()
    assert "Notion connector" in capsys.readouterr().out


def test_publish_cli_rejects_invalid(tmp_path):
    p = tmp_path / "ranked.json"
    p.write_text(json.dumps({"week_of": "x"}))
    with pytest.raises(SystemExit):
        main(["publish", str(p)])


def test_episode_md_front_matter():
    from datetime import datetime, timezone
    e = Episode("yt:abc", "s", "S", 2, "A: B", "https://u", datetime(2026, 10, 19, tzinfo=timezone.utc), youtube_id="abc")
    md = episode_md(e, [{"start": 65, "speaker": "Joe", "text": "hi"}], "youtube-captions")
    assert md.startswith("---\nepisode_id: yt:abc\n")
    assert "transcript_source: youtube-captions" in md and "[0:01:05] Joe: hi" in md
    assert "_No transcript found._" in episode_md(e, None, None)
    assert slug("Hello, World! 2026") == "hello-world-2026"
