import re

COMPANIES = {
    "nvidia": "NVDA", "amd": "AMD", "intel": "INTC", "tsmc": "TSM", "taiwan semiconductor": "TSM",
    "broadcom": "AVGO", "micron": "MU", "sk hynix": "000660.KS", "samsung": "005930.KS", "asml": "ASML",
    "applied materials": "AMAT", "lam research": "LRCX", "kla": "KLAC", "arm": "ARM", "qualcomm": "QCOM",
    "marvell": "MRVL", "coreweave": "CRWV", "nebius": "NBIS", "oracle": "ORCL", "microsoft": "MSFT",
    "google": "GOOGL", "alphabet": "GOOGL", "amazon": "AMZN", "aws": "AMZN", "meta": "META",
    "facebook": "META", "apple": "AAPL", "tesla": "TSLA", "netflix": "NFLX", "palantir": "PLTR",
    "snowflake": "SNOW", "datadog": "DDOG", "cloudflare": "NET", "crowdstrike": "CRWD", "salesforce": "CRM",
    "servicenow": "NOW", "shopify": "SHOP", "uber": "UBER", "spotify": "SPOT", "vertiv": "VRT",
    "super micro": "SMCI", "supermicro": "SMCI", "dell": "DELL", "arista": "ANET", "vistra": "VST",
    "constellation energy": "CEG", "ge vernova": "GEV", "eaton": "ETN", "cisco": "CSCO", "ibm": "IBM",
    "adobe": "ADBE", "robinhood": "HOOD", "coinbase": "COIN", "jpmorgan": "JPM", "goldman": "GS",
    "berkshire": "BRK.B", "walmart": "WMT", "costco": "COST", "eli lilly": "LLY", "novo nordisk": "NVO",
    "pure storage": "PSTG", "western digital": "WDC", "seagate": "STX", "astera labs": "ALAB",
    "credo": "CRDO", "coherent": "COHR", "lumentum": "LITE", "applovin": "APP", "openai": "private:OpenAI",
    "anthropic": "private:Anthropic", "spacex": "private:SpaceX", "xai": "private:xAI",
}
_NAME_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, COMPANIES), key=len, reverse=True)) + r")\b", re.I)
_CASHTAG = re.compile(r"\$([A-Z]{1,5})\b")


_NEEDS_CAPS = {"arm", "meta", "kla", "apple", "dell", "credo", "coherent", "uber", "goldman", "xai"}


def find_tickers(text: str) -> list[str]:
    found = []
    for m in _NAME_RE.finditer(text):
        word = m.group(1)
        if word.lower() in _NEEDS_CAPS and not word[0].isupper():
            continue
        found.append(COMPANIES[word.lower()])
    found += _CASHTAG.findall(text)
    return list(dict.fromkeys(found))
