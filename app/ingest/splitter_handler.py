from typing import List, Dict, Tuple, Optional, Any, Callable
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    MarkdownHeaderTextSplitter,
    CharacterTextSplitter,
    TokenTextSplitter,
)
from app.core.logger import logger
import re


class TextSplitter:
    def __init__(
        self,
        split_type: str = "lex_markdown",
        chunk_size: int = 2000,
        chunk_overlap: int = 200,
        fixed_base_headers: Optional[Dict[str, str]] = None,
    ):
        self.split_type = split_type
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.fixed_base_headers = fixed_base_headers or {}

        # Strategy pattern
        self.splitters: Dict[str, Callable[[str], List[Any]]] = {
            "markdown": self._markdown_split,
            "recursive": self._recursive_split,
            "character": self._character_split,
            "token": self._token_split,
            "custom": self._lex_markdown_split,
        }

    def split_text(self, text: str) -> List[Any]:
        """Split input text into chunks."""
        try:
            splitter = self.splitters.get(self.split_type, self._recursive_split)
            return splitter(text)
        except Exception as e:
            logger.error(
                f"[SplitterHandler] Splitting failed: {e}. Falling back to recursive splitter."
            )
            return self._recursive_split(text)

    # Built-in splitters
    def _recursive_split(self, text: str) -> List[str]:
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        return splitter.split_text(text)

    def _markdown_split(self, text: str) -> List[str]:
        try:
            md_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[("#", "Header 1"), ("##", "Header 2")],
                strip_headers=False,
                return_each_line=False,
            )
            docs = md_splitter.split_text(text)

            if not docs:
                logger.info("[SplitterHandler] Markdown produced no chunks. Fallback.")
                return self._recursive_split(text)

            rec_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
                chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
            )
            chunks = rec_splitter.split_documents(docs)
            return [doc.page_content.strip() for doc in chunks if doc.page_content.strip()]
        except Exception as e:
            logger.error(f"[SplitterHandler] Markdown error: {e}. Fallback.")
            return self._recursive_split(text)

    def _character_split(self, text: str) -> List[str]:
        splitter = CharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        return splitter.split_text(text)

    def _token_split(self, text: str) -> List[str]:
        splitter = TokenTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
        )
        return splitter.split_text(text)

    # Helpers
    @staticmethod
    def _clean_hdr_name(name: str) -> str:
        """Remove trailing {-id ... -id} blocks from header names."""
        return re.sub(r"\s*\{\s*(?:-\d+\s*)+\}\s*$", "", name).strip()

    def _parse_header_ids(self, text: str) -> Dict[Tuple[str, str], str]:
        """Extract header IDs from markdown text."""
        pattern = re.compile(
            r"^(#{1,4})\s+(.+?)\s*\{\s*((?:-\d+\s*)+)\}\s*$", re.MULTILINE
        )
        header_map: Dict[Tuple[str, str], str] = {}

        for match in pattern.finditer(text):
            level, raw_title, id_blob = match.groups()
            ids = re.findall(r"-\d+", id_blob)
            chosen_id = ids[-1] if ids else None
            if chosen_id:
                clean_title = self._clean_hdr_name(raw_title)
                header_map[(level, clean_title)] = chosen_id
        return header_map

    def _resolve_roots(
        self, splits: List[Any], base_headers: Optional[Dict[str, str]]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Resolve root headers from provided base_headers, fixed defaults, or content."""
        root_h1 = (base_headers or {}).get("h1") or self.fixed_base_headers.get("h1")
        root_h2 = (base_headers or {}).get("h2") or self.fixed_base_headers.get("h2")

        if root_h1 and root_h2:
            return root_h1.strip(), root_h2.strip()

        for s in splits:
            meta = getattr(s, "metadata", {}) or {}
            if not root_h1 and meta.get("form"):
                root_h1 = self._clean_hdr_name(meta["form"])
            if not root_h2 and meta.get("title"):
                root_h2 = self._clean_hdr_name(meta["title"])
            if root_h1 and root_h2:
                break

        return root_h1, root_h2

    def _lex_markdown_split(
        self, text: str, character_limit: int = 2000, base_headers: Optional[Dict[str, str]] = None
    ) -> List[Dict[str, Any]]:
        headers_to_split_on = [
            ("#", "form"),
            ("##", "title"),
            ("###", "header"),
            ("####", "clause"),
        ]

        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        rec_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap, length_function=len
        )

        header_id_map = self._parse_header_ids(text)
        splits = md_splitter.split_text(text)
        root_h1, root_h2 = self._resolve_roots(splits, base_headers)

        final_chunks: List[Dict[str, Any]] = []

        def emit(chunk_text: str, meta: Dict[str, Any], headers: Dict[str, str], anchor: str):
            header_lines = [
                f"# {headers['h1']}" if headers["h1"] else "",
                f"## {headers['h2']}" if headers["h2"] else "",
                f"### {headers['h3']}" if headers["h3"] else "",
                f"#### {headers['h4']}" if headers["h4"] else "",
            ]
            header_block = "\n".join(filter(None, header_lines))

            hierarchy_path = " / ".join(
                filter(None, [headers["h1"], headers["h2"], headers["h3"], headers["h4"]])
            )

            full_text = f"{header_block}\n\n{chunk_text}".strip() if header_block else chunk_text.strip()
            final_chunks.append(
                {
                    "text": full_text,
                    "metadata": {
                        **meta,
                        "root_h1": headers["h1"],
                        "root_h2": headers["h2"],
                        "root_h3": headers["h3"],
                        "root_h4": headers["h4"],
                        "hierarchy_path": hierarchy_path,
                        "anchor_id": anchor,
                    },
                }
            )

        for s in splits:
            content, meta = s.page_content, s.metadata or {}
            headers = {
                "h1": self._clean_hdr_name(meta.get("form", "")) or root_h1 or "",
                "h2": self._clean_hdr_name(meta.get("title", "")) or root_h2 or "",
                "h3": self._clean_hdr_name(meta.get("header", "")),
                "h4": self._clean_hdr_name(meta.get("clause", "")),
            }

            # Anchor ID selection by depth
            anchor = ""
            for lvl in ("####", "###", "##", "#"):
                title = headers.get({"#": "h1", "##": "h2", "###": "h3", "####": "h4"}[lvl])
                if title and (lvl, title) in header_id_map:
                    anchor = header_id_map[(lvl, title)]
                    break

            if len(content) > character_limit:
                for chunk in rec_splitter.split_text(content):
                    emit(chunk, meta, headers, anchor)
            else:
                emit(content, meta, headers, anchor)

        return final_chunks
