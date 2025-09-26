import os
import pandas as pd
import re
import asyncio
from typing import Dict, List
import hashlib

from app.db.db_manager import DBManager
from app.retrieval.embedding_manager import EmbeddingManager
from app.ingest.splitter_handler import TextSplitter
from app.models.ingest import ReleaseData
from app.core.config import settings
from app.core.logger import logger


class IngestionService:
    def __init__(self, 
                 character_limit: int = 2000,
                 partition_name: str = "without-modda",
                 mongo_upsert: bool = True):
        """
        Args:
            batch_size: upsert batch size to vector DB
            character_limit: pre-split threshold for a section
            mongo_upsert: whether to upsert to MongoDB
            partition_name: name of the partition to upsert to
        """
        self.mongo_upsert = mongo_upsert
        self.character_limit = character_limit
        self.partition_name = partition_name
        
        # Default empty metadata
        self.base_url = "https://lex.uz/docs/-{}"
        self.splitter = TextSplitter(split_type='lex_markdown')
        self.db_manager = DBManager()
        self.embedding_manager = EmbeddingManager()

    async def ingest_markdown(self, content: str, 
                                    doc_id: int,
                                    metadata: Dict[str, str] = None) -> bool:   
        """Process a single markdown document: full doc to Mongo, chunks to Vector DB."""
        try:
            url = self.base_url.format(doc_id) if doc_id is not None else ""

            release_data = self._extract_release_data(content)
            cleaned_content = self._clean_content(content)
            roots = self._detect_base_headers(cleaned_content)
            
            root_h1 = roots.get("h1") or ""
            root_h2 = roots.get("h2") or ""
            
            doc_path = " / ".join([p for p in [root_h1, root_h2] if p])

            base_metadata = {
                **vars(release_data),
                'url': url,
                'hierarchy_path': doc_path,
            }

            # Insert full doc with hierarchy metadata
            full_doc = {
                'text': cleaned_content,
                'metadata': base_metadata,
            }

            # Insert full doc into MongoDB
            if self.mongo_upsert:
                inserted_ids = self.db_manager.insert_documents(
                    collection_name=settings.COLLECTION_NAME,
                    documents=[full_doc]
                )
                parent_mongo_id = str(inserted_ids[0]) if inserted_ids else None
                logger.debug(f"[LexUzHierarchicalIngestor] Inserted Document to MongoDB with ID: {parent_mongo_id}")
                
            else:
                parent_mongo_id = None

            # Split for vector DB chunks using hierarchical lex splitter (with overlap)
            splits = self._split_content(cleaned_content)

            chunk_docs: List[Dict[str, any]] = []
            for idx, split in enumerate(splits):
                text = (split['text'] if isinstance(split, dict) else str(split)).strip()
                split_meta = (split.get('metadata', {}) if isinstance(split, dict) else {})

                # build per-chunk URL anchor id 
                anchor_id = split_meta.get("anchor_id") or ""
                chunk_url = ""
                if doc_id is not None:
                    base = self.base_url.format(doc_id) 
                    chunk_url = f"{base}{('#' + anchor_id) if anchor_id else ''}"

                metadata = {
                    **base_metadata,    
                    **split_meta,      
                    'chunk_index': idx,
                    'article_number': self.extract_article_number(split_meta.get('clause', ''))
                }
                if parent_mongo_id:
                    metadata['parent_mongo_id'] = parent_mongo_id
                if chunk_url:
                    metadata['url'] = chunk_url
                    metadata['chunk_url'] = chunk_url

                chunk_docs.append({
                    'id': hashlib.md5(text.encode('utf-8')).hexdigest(),
                    'text': text,         
                    'metadata': metadata
                })

            if not chunk_docs:
                logger.debug("[IngestionService] No chunks produced for file; skipping upsert/log.")
                return True

            # Embed and upsert chunks into vector DB
            texts = [doc['text'] for doc in chunk_docs]
            
            embeddings = self.embedding_manager.embed_batch(texts)

            for doc, emb in zip(chunk_docs, embeddings):
                doc['embedding'] = emb

            self.db_manager.upsert_vectors(documents=chunk_docs, partition_name=self.partition_name)

            logger.debug(f"[IngestionService] Upserted {len(chunk_docs)} chunks to {settings.VECTOR_DB_TYPE}")
            return True

        except Exception as e:
            logger.error(f"[IngestionService] Error in ingest_single for: {str(e)}")
            return False

    def _extract_release_data(self, content: str) -> ReleaseData:
        """Extract release data from the @@@ section in the content."""
        release_pattern = r'@@@\s*(.+?)(?=\n|$)'
        match = re.search(release_pattern, content, re.IGNORECASE)
        
        if not match:
            return ReleaseData(date=None, document_number=None, location=None)
        
        release_text = match.group(1).strip()
        date_pattern = r'(\d{4})-yil\s+(\d{1,2})-(\w+)'
        number_pattern = r'(\d+-\w+-son)'
        
        date_match = re.search(date_pattern, release_text)
        number_match = re.search(number_pattern, release_text)
        
        location = 'Toshkent sh.' if 'Toshkent' in release_text else None
        
        return ReleaseData(
            date=date_match.group(0) if date_match else None,
            document_number=number_match.group(0) if number_match else None,
            location=location
        )

    def _clean_content(self, content: str) -> str:
        """Remove @@@ section from content."""
        return re.sub(r'@@@\s*.+?(?=\n|$)', '', content, flags=re.IGNORECASE)
    
    def _detect_base_headers(self, content: str) -> dict:
        """
        Detect first H1 and first H2 in the document.
        Returns {"h1": str|None, "h2": str|None}
        """
        from langchain_text_splitters import MarkdownHeaderTextSplitter
        headers_to_split_on = [
            ("#", "form"),
            ("##", "title"),
            ("###", "header"),
            ("####", "clause"),
        ]
        splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        splits = splitter.split_text(content)

        detected_h1 = None
        detected_h2 = None
        for s in splits:
            meta = getattr(s, "metadata", {}) or {}
            if not detected_h1 and meta.get("form"):
                detected_h1 = meta["form"].strip()
            if not detected_h2 and meta.get("title"):
                detected_h2 = meta["title"].strip()
            if detected_h1 and detected_h2:
                break

        return {"h1": detected_h1, "h2": detected_h2}

    def extract_article_number(self, clause: str) -> int:
        """
        From a string like '93-modda. {-6445785}' returns 93 as int.
        Ignores numbers inside {...}.
        """
        # remove any {...} parts
        cleaned = re.sub(r'\{[^}]*\}', '', clause)
        # find first integer outside braces
        m = re.search(r'\d+', cleaned)
        return int(m.group()) if m else None

    def _split_content(self, content: str) -> List[Dict]:
        """
        Split content using markdown splitter with character limit and
        per-document base headers (first H1 + first H2).
        """
        base_headers = self._detect_base_headers(content)  # NEW: detect per doc
        return self.splitter._lex_markdown_split(
            content,
            self.character_limit,
            base_headers=base_headers,  # pass per-document roots
        )
        
    def _detect_base_headers(self, content: str) -> dict:
        from langchain_text_splitters import MarkdownHeaderTextSplitter
        headers_to_split_on = [
            ("#", "form"),
            ("##", "title"),
            ("###", "header"),
            ("####", "clause"),
        ]
        splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
        splits = splitter.split_text(content)

        def _clean_hdr(s: str | None) -> str | None:
            if not s:
                return s
            # remove trailing "{-123 ... -456}" with arbitrary spaces
            return re.sub(r"\s*\{\s*(?:-\d+\s*)+\}\s*$", "", s).strip()
        detected_h1 = None
        detected_h2 = None
        for s in splits:
            meta = getattr(s, "metadata", {}) or {}
            if not detected_h1 and meta.get("form"):
                detected_h1 = _clean_hdr(meta["form"])
            if not detected_h2 and meta.get("title"):
                detected_h2 = _clean_hdr(meta["title"])
            if detected_h1 and detected_h2:
                break

        return {"h1": detected_h1, "h2": detected_h2}
