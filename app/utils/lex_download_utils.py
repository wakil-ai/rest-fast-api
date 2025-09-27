import re
import asyncio
import urllib.parse as up
from pathlib import Path
from typing import Optional, Tuple

import aiofiles
import httpx
from bs4 import BeautifulSoup
from slugify import slugify
from app.models.lex_download_models import DLResult

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
TIMEOUT = 30.0

# ---------- helpers ----------
def _normalize_docs_url(url: str) -> str:
    u = up.urlparse(url)
    parts = [p for p in u.path.split("/") if p not in ("", "ru", "uz", "uzc", "eng", "en", "o'zb", "oʻzb", "oz")]
    path = "/" + "/".join(parts)
    return up.urlunparse((u.scheme, u.netloc, path, "", "", ""))

def _extract_doc_id(url: str) -> Optional[str]:
    m = re.search(r"/docs/(-?\d+)", url)
    return m.group(1) if m else None

def _safe_stem(name: str, maxlen: int = 90) -> str:
    s = slugify(name or "", lowercase=False, separator="_")
    return s[:maxlen].rstrip("_") or "lexuz_document"

def _guess_ext(url: str, content_type: str) -> str:
    u = url.lower()
    ct = (content_type or "").lower()
    if u.endswith(".doc") or "msword" in ct:
        return ".doc"
    if u.endswith(".docx") or "officedocument.wordprocessingml" in ct:
        return ".docx"
    return ".docx"

# parse the page’s JS: downloadDoc()
DOWNLOADDOC_FUNC_RE = re.compile(
    r"(?:function\s+downloadDoc\s*\([^)]*\)\s*{[^}]+}|downloadDoc\s*=\s*function\s*\([^)]*\)\s*{[^}]+})",
    re.IGNORECASE | re.DOTALL
)
URL_IN_FUNC_RE = re.compile(
    r"""(?:
            location\.href\s*=\s*|
            window\.open\s*\(\s*
        )
        (['"])(?P<url>.+?)\1
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE
)

async def _find_downloaddoc_target(client: httpx.AsyncClient, page_url: str, html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    # inline <script>
    for s in soup.find_all("script"):
        code = s.string or getattr(s, "text", "") or ""
        mfunc = DOWNLOADDOC_FUNC_RE.search(code)
        if mfunc:
            mu = URL_IN_FUNC_RE.search(mfunc.group(0))
            if mu:
                return up.urljoin(page_url, mu.group("url"))

    # external <script src=...>
    for s in soup.find_all("script", src=True):
        src = up.urljoin(page_url, s["src"])
        try:
            r = await client.get(src, timeout=TIMEOUT)
            r.raise_for_status()
            code = r.text
            mfunc = DOWNLOADDOC_FUNC_RE.search(code)
            if mfunc:
                mu = URL_IN_FUNC_RE.search(mfunc.group(0))
                if mu:
                    return up.urljoin(page_url, mu.group("url"))
        except Exception:
            continue
    return None

async def _download_file(client: httpx.AsyncClient, url: str, out_dir: Path, stem: str) -> Tuple[bool, Optional[Path], str]:
    try:
        async with client.stream("GET", url, timeout=TIMEOUT, follow_redirects=True) as r:
            r.raise_for_status()
            ext = _guess_ext(url, r.headers.get("Content-Type", ""))
            out = out_dir / f"{stem}{ext}"
            out_dir.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(out, "wb") as f:
                async for chunk in r.aiter_bytes():
                    if chunk:
                        await f.write(chunk)
        # sanity check
        stat = await asyncio.to_thread(out.stat)
        if stat.st_size < 1024:
            await asyncio.to_thread(out.unlink, True)
            return False, None, "too_small"
        return True, out, "ok"
    except Exception as e:
        return False, None, f"err:{e}"
    
# ---------- core ----------
async def _process_one(client: httpx.AsyncClient, name: str, url: str, out_dir: Path, prefer_doc: bool, delay_ms: int) -> DLResult:
    base = _normalize_docs_url(url)
    doc_id = _extract_doc_id(base) or "noid"
    stem = f"{doc_id}"

    # Pass 1: direct ?type=
    order = ("?type=doc", "?type=docx") if prefer_doc else ("?type=docx", "?type=doc")
    for q in order:
        ok, path, _ = await _download_file(client, base + q, out_dir, stem)
        if ok:
            if delay_ms: await asyncio.sleep(delay_ms/1000)
            return DLResult(name=name, url=url, saved=True, filename=str(path))

    # Pass 2: parse page for downloadDoc()
    try:
        rp = await client.get(base, timeout=TIMEOUT)
        rp.raise_for_status()
        target = await _find_downloaddoc_target(client, base, rp.text)
        if target:
            ok, path, _ = await _download_file(client, target, out_dir, stem)
            if ok:
                if delay_ms: await asyncio.sleep(delay_ms/1000)
                return DLResult(name=name, url=url, saved=True, filename=str(path))
    except Exception as e:
        return DLResult(name=name, url=url, saved=False, why=f"page_err:{e}")

    # Pass 3: last-ditch guess
    parsed = up.urlparse(base)
    guess = up.urlunparse((parsed.scheme, parsed.netloc, f"{parsed.path}/export", "", "type=docx", ""))
    ok, path, _ = await _download_file(client, guess, out_dir, stem)
    if ok:
        if delay_ms: await asyncio.sleep(delay_ms/1000)
        return DLResult(name=name, url=url, saved=True, filename=str(path))

    return DLResult(name=name, url=url, saved=False, why="no_word_export_found")