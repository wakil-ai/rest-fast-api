from app.utils.mapping import CLASS_TO_MD, WORD_HTML_HINTS, HEADING_FROM_MARK, _first_mapped_class
from bs4 import BeautifulSoup, NavigableString, Tag as BsTag
from typing import Iterable, List, Tuple
from pathlib import Path
import re
import aiofiles, asyncio

# ---------- small utils ----------
def _is_word_html(txt: str) -> bool:
    head = (txt[:8000] or "").lower()
    return any(h.lower() in head for h in WORD_HTML_HINTS)

def _safe_slug(s: str, maxlen: int = 120) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^\w\s\-\u0400-\u04FF]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return (s or "lexuz_document")[:maxlen]

def _unwrap_anchor_text(root: BsTag):
    # Preserve <a id="..."> anchors so we can collect IDs;
    # unwrap only anchors WITHOUT an id.
    for a in root.find_all("a"):
        if a.has_attr("id"):
            continue
        if len(a.contents) == 1 and isinstance(a.contents[0], NavigableString):
            a.replace_with(a.contents[0])

def _drop_boilerplate(root: BsTag):
    for sel in ("script", "style", ".OFFICIAL_SOUR_TEXT"):
        for n in root.select(sel):
            n.decompose()

def _get_text_clean(el: BsTag) -> str:
    return el.get_text(" ", strip=True)

def _collect_anchor_ids(el: BsTag) -> List[str]:
    """Collect all descendant <a id=""> plus node's own id in order, de-duped."""
    seen = set()
    out: List[str] = []
    if isinstance(el, BsTag) and el.has_attr("id"):
        _id = str(el.get("id")).strip()
        if _id and _id not in seen:
            out.append(_id); seen.add(_id)
    if not isinstance(el, BsTag):
        return out
    for a in el.find_all("a", id=True):
        _id = str(a.get("id")).strip()
        if _id and _id not in seen:
            out.append(_id); seen.add(_id)
    return out

def _ids_suffix(ids: List[str]) -> str:
    """Render IDs as ' {id1 id2}' (no #) or '' if none."""
    ids = [i for i in ids if i]
    return f" {'{' + ' '.join(ids) + '}'}" if ids else ""

def _merge_ids(*lists: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for lst in lists:
        for x in lst or []:
            if x and x not in seen:
                out.append(x); seen.add(x)
    return out

# ---------- special merges ----------
def _merge_act_form_and_accepting_body(main: BsTag, soup: BeautifulSoup):
    """
    Merge ACT_FORM + ACCEPTING_BODY into a single <h1>.
    Text order: ACCEPTING_BODY first, then ACT_FORM.
    Append all collected IDs to the H1 as inline text suffix ' {id1 id2}'.
    """
    for node in list(main.select(".ACCEPTING_BODY")):
        # Find next non-whitespace sibling tag
        sib = None
        for s in node.find_next_siblings():
            if isinstance(s, NavigableString) and not s.string.strip():
                continue  # Skip whitespace
            if isinstance(s, BsTag):
                sib = s
            break

        if sib and "ACT_FORM" in (sib.get("class") or []):
            # Based on observed output, the desired text order is ACCEPTING_BODY then ACT_FORM.
            merged_text = " ".join(
                x for x in [_get_text_clean(node), _get_text_clean(sib)] if x
            ).strip()
            ids = _merge_ids(_collect_anchor_ids(node), _collect_anchor_ids(sib))
            if merged_text:
                h1 = soup.new_tag("h1")
                h1.string = merged_text + _ids_suffix(ids)
                node.insert_before(h1)
            # Decompose both nodes to prevent them from being processed again
            sib.decompose()
            node.decompose()

def _merge_essentials(main: BsTag, soup: BeautifulSoup):
    """
    Combine consecutive ACT_ESSENTIAL_ELEMENTS / _NUM into one emphasized paragraph.
    IDs are not appended here (per requirement: only headings get IDs).
    """
    run: List[BsTag] = []
    def flush():
        nonlocal run
        if not run:
            return
        text = " ".join(_get_text_clean(e) for e in run if _get_text_clean(e)).strip()
        if text:
            p = soup.new_tag("p"); em = soup.new_tag("em"); em.string = text; p.append(em)
            run[0].insert_before(p)
        for e in run: e.decompose()
        run = []

    for el in list(main.descendants):
        if not isinstance(el, BsTag):
            continue
        cls = (getattr(el, "attrs", {}) or {}).get("class") or []
        if any(c in ("ACT_ESSENTIAL_ELEMENTS", "ACT_ESSENTIAL_ELEMENTS_NUM") for c in cls):
            run.append(el)
        else:
            flush()
    flush()



def _has_table(el: BsTag) -> bool:
    return isinstance(el, BsTag) and bool(el.find("table"))

def _apply_class_mapping(main: BsTag, soup: BeautifulSoup):
    """
    Convert classed blocks into semantic tags (h1..h4 or p/strong/em) per CLASS_TO_MD.
    For ANY heading (#, ##, ###, ####), append collected IDs as trailing inline text: ' {id1 id2}'.
    """
    nodes: List[BsTag] = [main] + list(main.find_all(True))
    for el in nodes:
        try:
            if not isinstance(el, BsTag):
                continue
            cls = _first_mapped_class(el)
            if not cls:
                continue
            mark = CLASS_TO_MD.get(cls, "")
            if mark == "@@@":
                # handled in _merge_essentials
                continue

            # collect IDs BEFORE replacing element
            ids = _collect_anchor_ids(el)

            if mark in HEADING_FROM_MARK:
                text = _get_text_clean(el)
                if not text:
                    el.decompose(); continue
                hname = HEADING_FROM_MARK[mark]
                h = soup.new_tag(hname)
                # Append IDs to ALL headings h1/h2/h3/h4
                h.string = text + _ids_suffix(ids)
                el.replace_with(h)
                continue

            if mark == "*":
                if _has_table(el): continue
                text = _get_text_clean(el)
                if not text: el.decompose(); continue
                p = soup.new_tag("p"); em = soup.new_tag("em"); em.string = text
                p.append(em); el.replace_with(p); continue

            if mark == "**":
                if _has_table(el): continue
                text = _get_text_clean(el)
                if not text: el.decompose(); continue
                p = soup.new_tag("p"); strong = soup.new_tag("strong"); strong.string = text
                p.append(strong); el.replace_with(p); continue

            if mark == "":
                if _has_table(el): continue
                if el.name != "p":
                    text = _get_text_clean(el)
                    if text:
                        p = soup.new_tag("p"); p.string = text; el.replace_with(p)
                    else:
                        el.decompose()
                continue
        except Exception:
            # best-effort: skip pathological node
            continue

# ---------- HTML → Markdown ----------
def _to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify as md
        md_text = md(html, heading_style="ATX", bullets="-", strip=["span", "label"])
        return re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    except Exception:
        import html2text
        conv = html2text.HTML2Text()
        conv.ignore_links = False; conv.ignore_images = True
        conv.body_width = 0; conv.single_line_break = True
        conv.unicode_snob = True; conv.protect_links = True
        conv.inline_links = True; conv.wrap_links = False
        conv.escape_snob = True; conv.ul_item_mark = "-"; conv.strong_mark = "**"
        conv.bypass_tables = False
        md_text = conv.handle(html)
        return re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"

# ---------- per-file worker ----------
async def _convert_one(path: Path, out_dir: Path) -> Tuple[str, bool, str]:
    try:
        txt = await asyncio.to_thread(path.read_text, "utf-8", "ignore")
    except Exception as e:
        return path.name, False, f"read_err:{e}"

    if not _is_word_html(txt):
        # not a Word-export HTML — skip
        return path.name, False, "skip_not_word_html"

    soup = BeautifulSoup(txt, "html.parser")
    main = soup.select_one("#divCont") or soup.body or soup

    _drop_boilerplate(main)
    _unwrap_anchor_text(main)                 # keep <a id="..."> intact
    _merge_act_form_and_accepting_body(main, soup)
    _merge_essentials(main, soup)
    _apply_class_mapping(main, soup)          # adds ID suffix to ALL headings produced here

    md = _to_markdown(str(main))

    # Preserve original filename stem (not title-based)
    stem = path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{stem}.md"
    async with aiofiles.open(out_path, "w", encoding="utf-8") as f:
        await f.write(md)

    return path.name, True, out_path.name

# ---------- FS helpers ----------
def _iter_inputs(src: Path) -> List[Path]:
    exts = {".doc", ".htm", ".html"}
    if src.is_file():
        return [src] if src.suffix.lower() in exts else []
    return [p for p in src.rglob("*") if p.suffix.lower() in exts]