from typing import List, Dict, Tuple, Optional, Any, Callable
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    MarkdownHeaderTextSplitter,
    CharacterTextSplitter,
    TokenTextSplitter,
)
from app.core.logger import logger
import tiktoken
from app.utils.text_cleaning import remove_braces
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
        
        headers_to_split_on = [
            ("###", "header"),
            ("####", "clause"),
        ]
        
        self.enc = tiktoken.get_encoding("cl100k_base")
        self.md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        self.rec_splitter = RecursiveCharacterTextSplitter(chunk_size=self.chunk_size,
                                                           chunk_overlap=self.chunk_overlap, 
                                                           length_function=lambda x: len(self.enc.encode(x)))

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

    def _lex_markdown_split(self, content: str) -> List[Dict]:
        """
        Split markdown content into chunks with metadata, respecting token limits.
        """
        default_header_block = self._build_header_block(content)
        markdown_chunks = self.md_splitter.split_text(content)
        processed_chunks = []

        for index, chunk in enumerate(markdown_chunks, 1):
            content_text = remove_braces(chunk.page_content)
            header = chunk.metadata.get("header", "")
            clause = chunk.metadata.get("clause", "")
            
            anchor_id = self._extract_anchor_id(clause or header)
            
            headers = ""
            if index != 1:
                headers += default_header_block + "\n" if index != 1 else ""
            if header:
                headers += f"### {header}\n"
            if clause:
                headers += f"#### {clause}\n"

            headers = remove_braces(headers).strip()
            full_text = (headers + content_text).strip()
            hierarchy_path = headers.replace("#", "").replace("\n", " > ").strip(" > ")
            token_count = self._count_tokens(full_text)

            if token_count > self.chunk_size:
                sub_chunks = self.rec_splitter.split_text(full_text)
                for sub_chunk in sub_chunks:
                    processed_chunks.append({
                        "text": sub_chunk,
                        "metadata": {
                            "default_header_block": default_header_block,
                            "header": header,
                            "clause": clause,
                            "anchor_id": anchor_id,
                            "chunk_index": index,
                            "hierarchy_path": hierarchy_path,
                            "article_number": self._extract_article_number(clause) if clause else None,
                        }
                    })
            else:
                processed_chunks.append({
                    "text": full_text,
                    "metadata": {
                        "default_header_block": default_header_block,
                        "header": header,
                        "clause": clause,
                        "anchor_id": anchor_id,
                        "chunk_index": index,
                        "hierarchy_path": hierarchy_path,
                        "article_number": self._extract_article_number(clause) if clause else None,
                    }
                })

        return processed_chunks
    
    # Helpers
    def _extract_anchor_id(self, text: str) -> str | None:
        """Extract anchor ID from text like '1-modda {-5664677}' or return None."""
        match = re.search(r'\{-?(\d+)\}', text)
        return match.group(1) if match else None

    def _extract_article_number(self, clause: str) -> int | None:
        """Extract article number from clause like '93-modda. {-6445785}' or return None."""
        match = re.search(r'\d+', clause)
        return int(match.group()) if match else None

    def _build_header_block(self, text: str) -> str:
        pattern = re.compile(r'^(#{1,2})\s+.*', re.MULTILINE)

        blocks = []
        for match in pattern.finditer(text):
            line = match.group(0).strip()  # full header line (# ... or ## ...)
            line = remove_braces(line).strip()  # Clean {-id ...} parts
            blocks.append(line)

        return "\n".join(blocks)
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in text using tiktoken."""
        return len(self.enc.encode(text))