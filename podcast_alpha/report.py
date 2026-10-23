from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from .digest import ts_link
from .ranked import FORBIDDEN, RankedError

SCHEMA = Path(__file__).resolve().parent.parent / "schema"


def validate(data: dict, kind: str) -> dict:
    try:
        jsonschema.validate(data, json.loads((SCHEMA / f"{kind}.schema.json").read_text()))
    except jsonschema.ValidationError as exc:
        raise RankedError(f"{kind}: {'/'.join(map(str, exc.absolute_path)) or 'root'}: {exc.message}") from None
    text = json.dumps(data).lower()
    if any(f'"{w}' in text for w in FORBIDDEN):
        raise RankedError(f"{kind}: holdings-like fields are not allowed")
    if kind == "selected":
        bad = [e["episode_id"] for e in data["episodes"] if e.get("fallback_text") and "fallback" not in e["why"].lower()]
        if bad:
            raise RankedError(f"selected: fallback_text picks must say 'fallback' in why: {bad}")
    if kind == "report":
        ids = [e["episode_id"] for e in data["essential"]]
        missing = [e["episode_id"] for e in data["episodes"] if e["episode_id"] not in ids]
        if missing:
            raise RankedError(f"report: episodes not in essential: {missing}")
        from .links import bad_url
        for e in data["essential"] + data["episodes"]:
            if e.get("listen_url") and (why := bad_url(e["listen_url"])):
                raise RankedError(f"report: {e['episode_id']} listen_url is {why}: {e['listen_url']}")
    return data


PARAPHRASE_WORDS = 12  # a run this long copied verbatim from the transcript is a quote, not a paraphrase
TS_SLACK = 30  # seconds a timestamp may run past the last transcript line


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


def _norm_name(name: str) -> str:
    return " ".join(_words(name))


def _load_transcript(run_dir: Path, row: dict) -> tuple[float | None, set[tuple[str, ...]]]:
    """(last timestamp in seconds or None, set of every PARAPHRASE_WORDS-word run) for the episode's .md."""
    from .fetch import segments_from_md

    md = run_dir / row["file"] if row.get("file") else None
    segs = segments_from_md(md.read_text()) if md and md.exists() else []
    if not segs:
        return None, set()
    words = _words(" ".join(re.sub(r"^(?:speaker\s*\d+:)\s*", "", s["text"], flags=re.I) for s in segs))
    n = PARAPHRASE_WORDS
    return max(s["start"] for s in segs), {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _longest_copy(text: str, grams: set[tuple[str, ...]]) -> list[str]:
    """The longest run of words from text that is also in the transcript (>= PARAPHRASE_WORDS), else []."""
    words, n = _words(text), PARAPHRASE_WORDS
    best: list[str] = []
    i = 0
    while i + n <= len(words):
        if tuple(words[i:i + n]) in grams:
            j = i + n
            while j < len(words) and tuple(words[j - n + 1:j + 1]) in grams:
                j += 1
            if j - i > len(best):
                best = words[i:j]
            i = j - n + 1
        i += 1
    return best


def _verify_selected(data: dict, by_id: dict) -> list[str]:
    errs, seen, owner = [], set(), {}
    for i, e in enumerate(data["episodes"]):
        eid, where = e["episode_id"], f"selected: episodes[{i}] {e['episode_id']}"
        if eid in seen:
            errs.append(f"{where}: duplicate episode_id (picks must be distinct)")
        seen.add(eid)
        row = by_id.get(eid)
        if row is None:
            errs.append(f"{where}: not in manifest.json")
            continue
        if row.get("rerun"):
            errs.append(f"{where}: manifest marks it rerun: true (replay/re-air)")
        if "has_transcript" in e and bool(e["has_transcript"]) != bool(row.get("has_transcript")):
            errs.append(f"{where}: has_transcript is {e['has_transcript']} but manifest says {bool(row.get('has_transcript'))}")
        if bool(e.get("fallback_text")) != bool(row.get("fallback_text")):
            errs.append(f"{where}: fallback_text is {bool(e.get('fallback_text'))} but manifest says {bool(row.get('fallback_text'))}")
        for g in e["guests"]:
            if "host" in (g.get("role") or "").lower() or not _norm_name(g["name"]):
                continue
            key = _norm_name(g["name"])
            if key in owner and owner[key] != eid:
                errs.append(f"{where}: guest {g['name']!r} also picked in {owner[key]} (one pick per person)")
            owner.setdefault(key, eid)
    return errs


def _cites(ep: dict):
    for i, c in enumerate(ep["signal"]):
        yield f"signal[{i}]", c
    for si, s in enumerate(ep["sections"]):
        for ci, c in enumerate(s.get("claims", [])):
            yield f"sections[{si}].claims[{ci}]", c


def _verify_report(data: dict, by_id: dict, run_dir: Path) -> list[str]:
    errs = []
    sel_p = run_dir / "selected.json"
    if sel_p.exists():
        try:
            want = {e["episode_id"] for e in json.loads(sel_p.read_text())["episodes"]}
        except (ValueError, KeyError, TypeError) as exc:
            errs.append(f"report: selected.json is unreadable ({exc})")
        else:
            for part in ("essential", "episodes"):
                got = {e["episode_id"] for e in data[part]}
                if got != want:
                    errs.append(f"report: {part} episode_ids differ from selected.json: "
                                f"missing {sorted(want - got)}, not selected {sorted(got - want)}")
    for ep in data["episodes"]:
        eid = ep["episode_id"]
        row = by_id.get(eid)
        if row is None:
            errs.append(f"report: {eid}: not in manifest.json")
            continue
        real = bool(row.get("has_transcript"))
        last, grams = _load_transcript(run_dir, row)
        if real and last is None:
            errs.append(f"report: {eid}: manifest has_transcript but {row.get('file')} has no [h:mm:ss] transcript lines")
        for where, c in _cites(ep):
            ts = c["timestamp"]
            if ts is None and real:
                errs.append(f"report: {eid} {where}: timestamp is null but the episode has a transcript")
            elif ts is not None and not real:
                errs.append(f"report: {eid} {where}: timestamp {ts} but the manifest says no real transcript (use null)")
            elif ts is not None and last is not None and ts > last + TS_SLACK:
                errs.append(f"report: {eid} {where}: timestamp {ts}s is past the end of the transcript "
                            f"({int(last)}s, +{TS_SLACK}s allowed)")
        fields = [(f"{w}.text", c["text"]) for w, c in _cites(ep)]
        fields += [(f"sections[{i}].body", s["body"]) for i, s in enumerate(ep["sections"])]
        for where, text in fields:
            if run := _longest_copy(text, grams):
                errs.append(f"report: {eid} {where}: {len(run)} consecutive words copied verbatim from the transcript "
                            f"(paraphrase; {PARAPHRASE_WORDS}+ is rejected): \"{' '.join(run[:PARAPHRASE_WORDS])}...\"")
    return errs


def verify(kind: str, data: dict, run_dir: Path) -> list[str]:
    """Check a schema-valid selected/report against the run folder. Deterministic, no network.
    Returns every problem found; [] when ok or when run_dir has no manifest.json (nothing to ground against)."""
    man_p = Path(run_dir) / "manifest.json"
    if not man_p.exists():
        return []
    run_dir = Path(run_dir)
    by_id = {e["episode_id"]: e for e in json.loads(man_p.read_text()).get("episodes", [])}
    if kind == "selected":
        return _verify_selected(data, by_id)
    if kind == "report":
        return _verify_report(data, by_id, run_dir)
    return []


NUM_WINDOW = 120  # seconds either side of a cite's timestamp in which each of its numbers should be spoken
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_NUM_TOKEN = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|[a-z]+")


def _num_key(v: float) -> str:
    return f"{v:g}"


def cite_numbers(text: str) -> list[str]:
    """Numbers worth checking in a cite: digits with commas/decimals; $ % B M T GW suffixes fall away.
    Whole numbers 0-9 and years 2000-2099 are skipped as too noisy (decimals like 1.5 are kept)."""
    out = []
    for tok in re.findall(r"(?<![A-Za-z\d.,])\d+(?:,\d{3})*(?:\.\d+)?", text.replace("-", " ")):  # not a16z, H100
        v = float(tok.replace(",", ""))
        whole = "." not in tok
        if whole and (v < 10 or 2000 <= v <= 2099):
            continue
        if (k := _num_key(v)) not in out:
            out.append(k)
    return out


def spoken_numbers(words: list[tuple[str, float]]) -> list[tuple[float, str]]:
    """Every number in a (word, timestamp) stream as (timestamp, key): digits, and spelled out ones
    (one..nineteen, twenty..ninety, hundred, 'twenty five', 'two hundred', 'one point four') as digits."""
    out, i, n = [], 0, len(words)
    while i < n:
        w, ts = words[i]
        if w[0].isdigit():
            out.append((ts, _num_key(float(w.replace(",", "")))))
            i += 1
            continue
        if w in _UNITS or w in _TENS or w == "hundred":
            cur, j = 0, i
            while j < n:
                x = words[j][0]
                r = cur % 100
                if x in _UNITS and (r == 0 or (r >= 20 and r % 10 == 0 and _UNITS[x] < 10)):
                    cur += _UNITS[x]
                elif x in _TENS and r == 0:
                    cur += _TENS[x]
                elif x == "hundred" and cur < 100:
                    cur = (cur or 1) * 100
                elif x == "and" and cur >= 100 and j + 1 < n and (words[j + 1][0] in _UNITS or words[j + 1][0] in _TENS):
                    pass
                else:
                    break
                j += 1
            val = str(int(cur))
            if j + 1 < n and words[j][0] == "point" and words[j + 1][0] in _UNITS and _UNITS[words[j + 1][0]] < 10:
                k = j + 1
                while k < n and words[k][0] in _UNITS and _UNITS[words[k][0]] < 10:
                    val += ("." if "." not in val else "") + str(_UNITS[words[k][0]])
                    k += 1
                j = k
            out.append((ts, _num_key(float(val))))
            i = j
            continue
        i += 1
    return out


def _clock(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _stream(segs: list[dict]) -> list[tuple[str, float]]:
    """(word, segment start) for every word of the segments; speaker prefixes and hyphens removed."""
    return [(w, s["start"]) for s in segs
            for w in _NUM_TOKEN.findall(re.sub(r"^(?:speaker\s*\d+:)\s*", "", s["text"], flags=re.I).lower().replace("-", " "))]


def _number_index(run_dir: Path, row: dict) -> list[tuple[float, str]]:
    from .fetch import segments_from_md

    md = run_dir / row["file"] if row.get("file") else None
    return spoken_numbers(_stream(segments_from_md(md.read_text()) if md and md.exists() else []))


def _number_warnings(ep: dict, index: list[tuple[float, str]], source: str = "transcript") -> list[str]:
    out = []
    for where, c in _cites(ep):
        ts = c["timestamp"]
        if ts is None:
            continue
        near = {k for t, k in index if abs(t - ts) <= NUM_WINDOW}
        for num in cite_numbers(c["text"]):
            if num in near:
                continue
            elsewhere = sorted({int(t) for t, k in index if k == num})
            found = (f"; spoken at {', '.join(_clock(t) for t in elsewhere[:6])}" + (" ..." if len(elsewhere) > 6 else "")
                     if elsewhere else f"; not found anywhere in the {source}")
            out.append(f"report: {ep['episode_id']} {where}: number {num} not spoken within {NUM_WINDOW}s of "
                       f"timestamp {ts} ({_clock(ts)}) in the {source}{found}")
    return out


def warn(kind: str, data: dict, run_dir: Path) -> list[str]:
    """Soft checks that never fail a run: each number (>= 10, not a 2000s year) in a report cite should be spoken
    within NUM_WINDOW seconds of the cite's timestamp. Catches in-range but wrong timestamps and misheard figures."""
    man_p = Path(run_dir) / "manifest.json"
    if kind != "report" or not man_p.exists():
        return []
    run_dir = Path(run_dir)
    by_id = {e["episode_id"]: e for e in json.loads(man_p.read_text()).get("episodes", [])}
    out = []
    for ep in data["episodes"]:
        row = by_id.get(ep["episode_id"])
        if row is not None and row.get("has_transcript"):
            out += _number_warnings(ep, _number_index(run_dir, row))
    return out


def _ranked_cites(e: dict) -> dict:
    return {"episode_id": e["episode_id"], "sections": [],
            "signal": [{"text": c["claim"], "timestamp": c["timestamp"]} for c in e["claims"]]}


def ranked_verify(data: dict, run_dir: Path) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a weekly ranked.json against its run folder; ([], []) when run_dir has no manifest.
    Errors: id not in the manifest, claims on an episode without a real transcript, timestamp past the transcript end,
    a 12-word verbatim run. Warnings: numbers in a claim not spoken within NUM_WINDOW seconds of its timestamp."""
    man_p = Path(run_dir) / "manifest.json"
    if not man_p.exists():
        return [], []
    run_dir = Path(run_dir)
    by_id = {e["episode_id"]: e for e in json.loads(man_p.read_text()).get("episodes", [])}
    errs, warns = [], []
    for e in data["episodes"]:
        eid, row = e["episode_id"], by_id.get(e["episode_id"])
        if row is None:
            errs.append(f"ranked: {eid}: not in manifest.json")
            continue
        if not row.get("has_transcript"):
            if e["claims"]:
                errs.append(f"ranked: {eid}: has claims but the manifest says no real transcript")
            continue
        last, grams = _load_transcript(run_dir, row)
        for i, c in enumerate(e["claims"]):
            if last is not None and c["timestamp"] > last + TS_SLACK:
                errs.append(f"ranked: {eid} claims[{i}]: timestamp {c['timestamp']}s is past the end of the transcript ({int(last)}s)")
            if run := _longest_copy(c["claim"], grams):
                errs.append(f"ranked: {eid} claims[{i}]: {len(run)} consecutive words copied verbatim from the transcript")
        warns += _number_warnings(_ranked_cites(e), _number_index(run_dir, row))
    return errs, [w.replace("report: ", "ranked: ", 1).replace(" signal[", " claims[") for w in warns]


OFFSET_MIN_SECONDS = 45  # median cite-vs-video drift that is reported
OFFSET_MIN_NUMBERS = 3
OFFSET_MAX_PAIR = 600  # a caption mention further than this from the cite is a different mention, not drift


def estimate_offset(ep: dict, index: list[tuple[float, str]]) -> tuple[float, int] | None:
    """Median of (cited - spoken) seconds over cite numbers, using each number's nearest mention. (median, samples)."""
    deltas = []
    for _, c in _cites(ep):
        if c["timestamp"] is None:
            continue
        for num in cite_numbers(c["text"]):
            spoken = [t for t, k in index if k == num]
            if not spoken:
                continue
            delta = c["timestamp"] - min(spoken, key=lambda t: abs(t - c["timestamp"]))
            if abs(delta) <= OFFSET_MAX_PAIR:
                deltas.append(delta)
    if not deltas:
        return None
    deltas.sort()
    mid = len(deltas) // 2
    return (deltas[mid] if len(deltas) % 2 else (deltas[mid - 1] + deltas[mid]) / 2), len(deltas)


def caption_warn(data: dict, run_dir: Path, fetch=None) -> list[str]:
    """Opt-in, network: compare cites with the linked YouTube video's captions (youtube_url in the report or
    selected.json). Same number-near-timestamp warnings plus a per-episode drift estimate. Never raises."""
    from .links import yt_id

    if fetch is None:
        from .transcribe import from_youtube as fetch
    sel = {}
    try:
        if (Path(run_dir) / "selected.json").exists():
            sel = {e["episode_id"]: e for e in json.loads((Path(run_dir) / "selected.json").read_text())["episodes"]}
    except (ValueError, KeyError, TypeError):
        pass
    out = []
    for ep in data["episodes"]:
        eid = ep["episode_id"]
        url = ep.get("youtube_url") or next((e.get("youtube_url") for e in data["essential"] if e["episode_id"] == eid), None) \
            or sel.get(eid, {}).get("youtube_url")
        vid = yt_id(url)
        if not vid:
            continue
        try:
            segs = fetch(vid)
        except Exception:
            segs = None
        if not segs:
            out.append(f"report: {eid}: could not fetch YouTube captions for {vid}; skipped")
            continue
        index = spoken_numbers(_stream(segs))
        out += _number_warnings(ep, index, "YouTube captions")
        est = estimate_offset(ep, index)
        if est and est[1] >= OFFSET_MIN_NUMBERS and abs(est[0]) >= OFFSET_MIN_SECONDS:
            out.append(f"report: episode {eid} timestamps run {round(abs(est[0]))} s {'later' if est[0] > 0 else 'earlier'} "
                       f"than the linked YouTube video (median over {est[1]} numbers)")
    return out


def cite(c: dict, url: str) -> str:
    where = ", ".join(x for x in (c["speaker"], ts_link(url, c["timestamp"]) if c["timestamp"] is not None else "") if x)
    read = f" (our read: {c['our_read']})" if c.get("our_read") else ""
    return f"- {c['text']} [{where}]{read}"


def attach_links(data: dict, run_dir: Path | None) -> dict:
    """Fill youtube_url / listen_url on essential and episodes from selected.json; never audio or tracking."""
    from .links import bad_url
    sel = {}
    if run_dir and (run_dir / "selected.json").exists():
        sel = {e["episode_id"]: e for e in json.loads((run_dir / "selected.json").read_text())["episodes"]}
    eps = {e["episode_id"]: e for e in data["episodes"]}
    for e in data["essential"] + data["episodes"]:
        s, ep = sel.get(e["episode_id"], {}), eps.get(e["episode_id"], {})
        for k in ("youtube_url", "listen_url"):
            if not e.get(k) and (v := s.get(k) or ep.get(k)):
                e[k] = v
        if not e.get("listen_url") and ep.get("url") and not bad_url(ep["url"]):
            e["listen_url"] = ep["url"]
    return data


def best_link(e: dict) -> str | None:
    """YouTube first, then Apple/Spotify/episode page (listen_url), then a non-audio url."""
    from .links import bad_url
    for u in (e.get("youtube_url"), e.get("listen_url"), e.get("url")):
        if u and not bad_url(u):
            return u
    return None


def essential_line(i: int, e: dict) -> str:
    head = f"[{e['headline']}]({u})" if (u := best_link(e)) else e["headline"]
    return f"{i}. **{head}** {e['show']}, with {e['guests']}"


def render(data: dict) -> str:
    lines = [f"# {data['title']}", "", "## Key Insights", ""] + [f"- {k}" for k in data["key_insights"]]
    lines += ["", "## Essential Episodes", ""]
    lines += [essential_line(i, e) for i, e in enumerate(data["essential"], 1)]
    for ep in data["episodes"]:
        head = f"[{ep['headline']}]({u})" if (u := best_link(ep)) else ep["headline"]
        lines += ["", "---", "", f"## {head}", "", "### THE SIGNAL", ""]
        lines += [cite(c, best_link(ep) or ep["url"]) for c in ep["signal"]]
        if ep.get("interests"):
            lines += ["", f"**Interests to note:** {ep['interests']}"]
        for n, s in enumerate(ep["sections"], 1):
            lines += ["", f"### {n:02d} {s['title']}", "", s["body"]]
            lines += [cite(c, best_link(ep) or ep["url"]) for c in s.get("claims", [])]
    lines += ["", "_AI-assisted summaries of public episodes. Verify before relying on them. Not investment advice._"]
    return "\n".join(lines) + "\n"
