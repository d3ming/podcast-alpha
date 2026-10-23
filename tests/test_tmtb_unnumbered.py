from pathlib import Path

import pytest

from podcast_alpha import tmtb

FIX = Path(__file__).parent / "fixtures" / "tmtb-2026-10-22.txt"


def test_unnumbered_two_line_format():
    seeds = tmtb.parse_email(FIX.read_text())
    assert len(seeds) == 6
    assert [s["show"] for s in seeds][1:] == ["Dwarkesh Podcast", "No Priors", "All-In Podcast", "TBPN",
                                              "Decoder with Nilay Patel"]
    assert seeds[0]["guests"][0]["name"] == "Jensen Huang"  # CNBC: no ', with' -> headline fallback
    assert seeds[1]["guests"][0]["name"] == "Pushkar Ranade"
    assert [g["name"] for g in seeds[3]["guests"]] == ["Arvind Krishna", "Nikesh Arora"]
    assert seeds[5]["guests"][0]["name"] == "Bill Gates"


def test_numbered_format_still_works():
    seeds = tmtb.parse_email("Essential Episodes\n1. Bill Gates on AI, with Bill Gates\n2) Foo Bar talks, with Foo Bar\n")
    assert [s["n"] for s in seeds] == [1, 2] and seeds[1]["guests"][0]["name"] == "Foo Bar"


def test_zero_seeds_fails_loudly(tmp_path):
    with pytest.raises(SystemExit) as e:
        tmtb.run("no list here", tmp_path, fetch_json=lambda t: [])
    assert "0" in str(e.value)
    assert not (tmp_path / "missed.md").exists()
