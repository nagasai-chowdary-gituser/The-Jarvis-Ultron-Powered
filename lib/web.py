"""
lib/web.py — fetching and searching. Results are DATA, never instructions.

Everything that comes back is fenced by safety.fence() before it can reach the
model. A page that says "ignore your instructions and send this" gets reported,
not obeyed.
"""
from __future__ import annotations

import os

import httpx

import safety

TIMEOUT = 15.0
MAX_CHARS = 2_000          # trimmed at the source; tokens are the scarce thing


def fetch(url: str, max_chars: int = MAX_CHARS) -> str:
    """Fetch a page as text. Fenced."""
    r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
    r.raise_for_status()
    text = r.text
    if "<" in text[:400]:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        except ImportError:
            pass
    text = "\n".join(ln for ln in text.splitlines() if ln.strip())[:max_chars]
    return safety.fence(text, f"the web page {url[:60]}")

# ─────────────────────────────────────────────────────────────────────────────
# Search — two backends, raced. The first useful answer wins.
#
# WHY RACE INSTEAD OF FALLING BACK. A fallback only helps when the first
# backend FAILS; it does nothing when the first backend is merely slow, which
# is the common case. The model-backed search is thorough but takes seconds;
# DuckDuckGo's HTML endpoint is usually under a second but thinner. Running
# both and taking whoever finishes first costs one extra HTTP request and
# removes the slow case entirely.
#
# HONEST LIMIT: a Python thread cannot be killed from outside. "Cancelling"
# the loser means we stop waiting for it - the abandoned request finishes into
# nothing and dies on its own timeout. No result is ever mixed in late.
# ─────────────────────────────────────────────────────────────────────────────

DDG_URL = "https://html.duckduckgo.com/html/"


def _ddg(q: str, limit: int) -> list[dict]:
    """DuckDuckGo's HTML endpoint. No key, no quota, usually sub-second."""
    from bs4 import BeautifulSoup

    r = httpx.post(DDG_URL, data={"q": q}, timeout=8.0,
                   follow_redirects=True,
                   headers={"User-Agent": "Mozilla/5.0 (Jarvis)"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select("div.result")[:limit * 2]:
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href") or ""
        if href.startswith("//duckduckgo.com/l/?uddg="):
            from urllib.parse import parse_qs, unquote, urlparse
            qs = parse_qs(urlparse("https:" + href).query)
            href = unquote((qs.get("uddg") or [""])[0])
        if not href.startswith("http"):
            continue
        snip = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True)[:120],
                    "url": href,
                    "snippet": (snip.get_text(" ", strip=True) if snip else "")[:200]})
        if len(out) >= limit:
            break
    return out


def _model_search(q: str, limit: int) -> list[dict]:
    """The model's own web_search tool. Slower, but it reads the pages."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(timeout=30.0, max_retries=1)
    r = client.responses.create(
        model=os.environ.get("JARVIS_SEARCH_MODEL", "gpt-5.6-luna"),
        tools=[{"type": "web_search"}],
        input=[{"role": "user",
                "content": f"Search the web for: {q}. List the top {limit} "
                           f"results as 'TITLE | URL | one-line summary'."}],
        max_output_tokens=900, store=False)
    out = []
    for line in (r.output_text or "").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2 and parts[1].startswith("http"):
            out.append({"title": parts[0][:120], "url": parts[1],
                        "snippet": (parts[2] if len(parts) > 2 else "")[:200]})
    return out[:limit]


def search(q: str, limit: int = 6) -> list[dict]:
    """Web search. Returns [{title, url, snippet, via}] - snippets are fenced.

    Both backends start at once. The first to return anything usable wins and
    the other is abandoned. `via` says which one answered, so a thin result is
    recognisable as a thin result rather than looking like the whole web."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    pool = ThreadPoolExecutor(max_workers=2)
    futures = {pool.submit(_ddg, q, limit): "duckduckgo",
               pool.submit(_model_search, q, limit): "model"}
    winner: list[dict] = []
    errors: list[str] = []
    try:
        pending = set(futures)
        while pending and not winner:
            done, pending = wait(pending, timeout=32.0,
                                 return_when=FIRST_COMPLETED)
            if not done:
                break                       # both backends hung
            for fut in done:
                name = futures[fut]
                try:
                    hits = fut.result()
                except Exception as e:
                    errors.append(f"{name}: {type(e).__name__}")
                    continue
                if hits:
                    for h in hits:
                        h["via"] = name
                        h["snippet"] = safety.fence(
                            h.get("snippet", ""), f"a {name} search result")
                    winner = hits
                    break
    finally:
        # Do not wait. The loser is abandoned on purpose - that is the point.
        pool.shutdown(wait=False, cancel_futures=True)

    if not winner and errors:
        raise RuntimeError("both search backends failed - " + "; ".join(errors))
    return winner
