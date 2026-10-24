from pathlib import Path

import pytest

from podcast_alpha import tmtb

FIX = Path(__file__).parent / "fixtures" / "tmtb-2026-10-22.txt"


def test_unnumbered_two_line_format():
    seeds = tmtb.parse_email(FIX.read_text())
    assert len(seeds) == 6
    assert [s["show"] for s in seeds][1:] == ["Deep Silicon Podcast", "Signal Hour", "Fourth Quarter Podcast",
                                              "Tech Brunch Live", "Open Channel with Jonas Reid"]
    assert seeds[0]["guests"][0]["name"] == "Orrin Vance"  # morning-show line: no ', with' -> headline fallback
    assert seeds[1]["guests"][0]["name"] == "Pavel Ostrander"
    assert [g["name"] for g in seeds[3]["guests"]] == ["Hollis Brandt", "Selene Park"]
    assert seeds[5]["guests"][0]["name"] == "Edith Marlowe"


def test_numbered_format_still_works():
    seeds = tmtb.parse_email("Essential Episodes\n1. Edith Marlowe on AI, with Edith Marlowe\n2) Foo Bar talks, with Foo Bar\n")
    assert [s["n"] for s in seeds] == [1, 2] and seeds[1]["guests"][0]["name"] == "Foo Bar"


def test_zero_seeds_fails_loudly(tmp_path):
    with pytest.raises(SystemExit) as e:
        tmtb.run("no list here", tmp_path, fetch_json=lambda t: [])
    assert "0" in str(e.value)
    assert not (tmp_path / "missed.md").exists()


def test_unnumbered_recall_hit_and_miss():
    seeds = tmtb.parse_email(FIX.read_text())
    manifest = {"episodes": [
        {"episode_id": "rss:a", "title": "Orrin Vance: Factory Financing and the Substrate Bottleneck", "source": "Morning Desk"},
        {"episode_id": "rss:b", "title": "Unrelated gardening hour", "source": "Green Thumb"}]}
    assert tmtb.covered(seeds[0], manifest) == ["rss:a"]  # headline-guest fallback seed matched by guest name
    assert tmtb.covered(seeds[1], manifest) == []  # Meridian/Ostrander seed: only an unrelated Orrin Vance episode is in the run
