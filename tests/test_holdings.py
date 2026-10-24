from podcast_alpha import holdings


def test_unset_env_returns_empty_without_network(monkeypatch, capsys):
    monkeypatch.delenv("PA_FOLIO_URL", raising=False)
    monkeypatch.setattr(holdings.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    assert holdings.fetch_weights() == {}
    assert "PA_FOLIO_URL not set" in capsys.readouterr().out


def test_env_url_is_used_and_not_printed(monkeypatch, capsys):
    seen = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"holdings": [{"ticker": "aaa", "market_pct": 10.0}]}

    monkeypatch.setenv("PA_FOLIO_URL", "https://folio.invalid/x")
    monkeypatch.setattr(holdings.requests, "get", lambda url, **k: seen.append(url) or R())
    assert holdings.fetch_weights() == {"AAA": 0.1}
    assert seen == ["https://folio.invalid/x"]
    assert "folio.invalid" not in capsys.readouterr().out
