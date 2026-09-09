# rag_engine.py
import os
import re
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


# ── Shared model singleton — loaded once, reused across all RAGEngine instances ─
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
_shared_model: Optional[SentenceTransformer] = None

def _get_shared_model() -> SentenceTransformer:
    global _shared_model
    if _shared_model is None:
        logger.info(f"Loading embedding model (once): {_EMBEDDING_MODEL_NAME}")
        try:
            _shared_model = SentenceTransformer(_EMBEDDING_MODEL_NAME, local_files_only=True)
            logger.info("Embedding model loaded from local cache")
        except Exception:
            logger.info("Downloading embedding model…")
            _shared_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
        logger.info("Embedding model ready — will be reused across all RAGEngine instances")
    return _shared_model


# ── RAGEngine instance cache — one per notebook_id, reused across requests ────
# Avoids reconnecting to ChromaDB on every API call.
_engine_cache: Dict[str, "RAGEngine"] = {}

def get_rag_engine(notebook_id: Optional[str] = None) -> "RAGEngine":
    """Return a cached RAGEngine for this notebook_id, creating one if needed."""
    key = notebook_id or "__default__"
    if key not in _engine_cache:
        _engine_cache[key] = RAGEngine(notebook_id=notebook_id)
    return _engine_cache[key]


class RAGEngine:
    """RAG engine with semantic chunking, multi-query retrieval, and context assembly."""

    def __init__(
        self,
        chroma_path: str = None,
        embedding_model: str = "all-MiniLM-L6-v2",
        chunk_size: int = 600,
        chunk_overlap: int = 120,
        top_k: int = 15,
        notebook_id: Optional[str] = None,
    ):
        if chroma_path is None:
            backend_dir = os.path.dirname(os.path.abspath(__file__))
            chroma_path = os.path.join(backend_dir, "data", "chroma_db")

        self.chroma_path   = Path(chroma_path)
        self.chroma_path.mkdir(parents=True, exist_ok=True)
        self.chunk_size    = chunk_size
        self.chunk_overlap = chunk_overlap
        self.top_k         = top_k
        self.notebook_id   = notebook_id

        self.chroma_client = chromadb.PersistentClient(
            path=str(self.chroma_path),
            settings=Settings(anonymized_telemetry=False),
        )

        # Collection name includes notebook_id for per-project isolation
        collection_name = f"documents_{notebook_id}" if notebook_id else "documents"
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        # Reuse the module-level singleton — avoids reloading 80MB model on every call
        self.model = _get_shared_model()

        logger.info(f"RAGEngine ready — ChromaDB at {self.chroma_path}, collection: {collection_name}")

    # ── Chunking ───────────────────────────────────────────────────────────

    def _chunk_text(self, text: str) -> List[str]:
        """
        Semantic-aware chunker that handles both web content and PPTX/document content.

        For web/plain text (## headings, paragraphs):
        - Splits on ## headings and double newlines
        - Each section becomes its own chunk set with heading prefix for context

        For PPTX/document content ([TABLE:], [CHART N], SLIDE N markers):
        - Splits on structural markers so charts/tables never bleed across chunks

        Within each block, builds overlapping chunks from sentences so context
        carries across chunk boundaries.
        """
        text = text.strip()
        if not text:
            return []

        # Detect content type — web content has ## headings, PPTX has slide markers,
        # PDFs have "PAGE N" markers (one per page, inserted by document_manager)
        has_pptx_markers = bool(re.search(r'\[(?:TABLE|CHART)[^\]]*\]|={10,}|SLIDE\s+\d+', text))
        has_page_markers = bool(re.search(r'^PAGE\s+\d+\b', text, re.MULTILINE))
        has_web_headings = bool(re.search(r'^##\s+.+', text, re.MULTILINE))

        if has_pptx_markers or has_page_markers:
            # ── PPTX/PDF path — split on structural markers, including page breaks ──
            block_pattern = re.compile(
                r'(?=\[(?:TABLE|CHART)[^\]]*\]|={10,}|SLIDE\s+\d+|^PAGE\s+\d+\b)',
                re.MULTILINE
            )
            raw_blocks = block_pattern.split(text)
        elif has_web_headings:
            # ── Web content path — split on ## headings ──
            raw_blocks = re.split(r'(?=^##\s+)', text, flags=re.MULTILINE)
        else:
            # ── Plain text — split on double newlines (paragraphs) ──
            raw_blocks = re.split(r'\n{2,}', text)

        blocks = [b.strip() for b in raw_blocks if b.strip()]

        all_chunks: List[str] = []

        for block in blocks:
            # Identify block header (first line if it's a heading/marker)
            lines = block.splitlines()
            header = ""
            first = lines[0].strip() if lines else ""
            if (first.startswith('[') or first.startswith('SLIDE') or
                    first.startswith('=') or first.startswith('##') or
                    re.match(r'^PAGE\s+\d+\b', first, re.IGNORECASE)):
                header = first

            # Split block into sentences / logical lines
            segments = re.split(r'\n{2,}|(?<=\.)\s+(?=[A-Z])', block)
            segments = [s.strip() for s in segments if s.strip()]

            if not segments:
                continue

            # Build overlapping chunks from segments
            current = []
            current_len = 0

            for seg in segments:
                seg_len = len(seg)
                if current_len + seg_len > self.chunk_size and current:
                    chunk_text = '\n'.join(current).strip()
                    if header and not chunk_text.startswith(header):
                        chunk_text = f"{header}\n{chunk_text}"
                    all_chunks.append(chunk_text)
                    # Overlap: keep last segment(s) for next chunk
                    overlap_chars = 0
                    keep = []
                    for prev in reversed(current):
                        overlap_chars += len(prev)
                        keep.insert(0, prev)
                        if overlap_chars >= self.chunk_overlap:
                            break
                    current = keep
                    current_len = sum(len(s) for s in current)

                current.append(seg)
                current_len += seg_len

            # Flush remaining
            if current:
                chunk_text = '\n'.join(current).strip()
                if header and not chunk_text.startswith(header):
                    chunk_text = f"{header}\n{chunk_text}"
                all_chunks.append(chunk_text)

        # Deduplicate while preserving order
        seen = set()
        unique: List[str] = []
        for c in all_chunks:
            key = c[:120]
            if key not in seen and len(c) > 10:
                seen.add(key)
                unique.append(c)

        return unique

    @staticmethod
    def _extract_page_number(chunk_text: str) -> Optional[int]:
        """
        Pull the page number out of a chunk's own text into structured
        metadata, so citations can reference it directly instead of a
        generic "Doc 1"/"Doc 2" label. Covers both the "PAGE N" marker
        document_manager inserts at the start of each PDF page, and a
        "[TABLE n — Page N]" style reference if that's what leads the chunk.
        """
        head = chunk_text.strip()[:200]
        m = re.match(r'^PAGE\s+(\d+)\b', head, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m2 = re.search(r'\bPage\s+(\d+)\b', head, re.IGNORECASE)
        if m2:
            return int(m2.group(1))
        return None

    # ── Ingest ─────────────────────────────────────────────────────────────

    def ingest_document(
        self,
        doc_id: str,
        text: str,
        metadata: Dict[str, Any] = None,
    ) -> int:
        """Chunk, embed, and store a document. Returns chunk count."""
        if not text.strip():
            logger.warning(f"Empty document skipped: {doc_id}")
            return 0

        self.remove_document(doc_id)
        chunks = self._chunk_text(text)
        if not chunks:
            return 0

        ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = []
        for i, c in enumerate(chunks):
            md = {"doc_id": doc_id, "chunk_index": i, "notebook_id": self.notebook_id, **(metadata or {})}
            page = self._extract_page_number(c)
            if page is not None:
                md["page"] = page
            metadatas.append(md)

        logger.info(f"Embedding {len(chunks)} chunks for {doc_id}…")
        embeddings = self.model.encode(chunks, batch_size=64, show_progress_bar=False)

        self.collection.add(
            ids=ids,
            embeddings=embeddings.tolist(),
            documents=chunks,
            metadatas=metadatas,
        )
        logger.info(f"Ingested {len(chunks)} chunks — {doc_id}")
        return len(chunks)

    # ── Multi-query retrieval ──────────────────────────────────────────────

    def _expand_query(self, query: str) -> List[str]:
        """
        Generate retrieval-optimised variants of the user query.
        Generic — works for web pages, documents, and any domain.
        """
        q = query.strip()
        if not q:
            return [q]

        variants = [q]
        ql = q.lower()

        # 1. Noun-phrase extraction: capitalised words and quoted phrases
        noun_phrases = re.findall(r'"([^"]+)"', q)
        noun_phrases += re.findall(r'\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\b', q)

        for phrase in noun_phrases[:3]:
            if phrase.lower() != q.lower() and len(phrase) > 2:
                variants.append(phrase)
                variants.append(phrase + " overview")
                variants.append(phrase + " details")

        # 2. Question → statement conversion
        q_clean = re.sub(r'^(what|how|why|when|where|who|which|can|does|is|are|do)\s+', '', ql, flags=re.I)
        q_clean = re.sub(r'\?$', '', q_clean).strip()
        if q_clean and q_clean != ql:
            variants.append(q_clean)

        # 3. Strip stopwords to get core search phrase
        stopwords = {"what", "how", "why", "when", "where", "who", "which",
                     "the", "a", "an", "is", "are", "was", "were", "be",
                     "can", "does", "do", "tell", "me", "about", "explain",
                     "describe", "show", "give", "find", "get"}
        core_words = [w for w in re.findall(r'\b\w+\b', ql) if w not in stopwords and len(w) > 2]
        if core_words:
            core = " ".join(core_words[:6])
            if core not in variants:
                variants.append(core)
            for i in range(len(core_words) - 1):
                pair = core_words[i] + " " + core_words[i + 1]
                if pair not in variants:
                    variants.append(pair)

        # 4. De-duplicate, filter empties, cap at 8
        seen: set = set()
        out: List[str] = []
        for v in variants:
            k = v.lower().strip()
            if k and k not in seen and len(v.strip()) > 1:
                seen.add(k)
                out.append(v.strip())
        return out[:8]

    def retrieve_context(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
        notebook_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Multi-query retrieval.

        Encodes the original query + expanded variants in one batch.
        Results from all variants are merged, deduplicated by chunk_id,
        and returned sorted by best relevance score.

        If notebook_id is provided and differs from self.notebook_id,
        use the appropriate collection for that notebook.
        """
        if not query.strip():
            return []

        k = top_k or self.top_k
        variants = self._expand_query(query)
        logger.info(f"Retrieving with {len(variants)} query variants: {variants}")

        embeddings = self.model.encode(variants, show_progress_bar=False)

        # If notebook_id is provided and different from self.notebook_id, get that collection
        collection = self.collection
        if notebook_id and notebook_id != self.notebook_id:
            collection_name = f"documents_{notebook_id}"
            try:
                collection = self.chroma_client.get_or_create_collection(
                    name=collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(f"Using notebook-specific collection: {collection_name}")
            except Exception as e:
                logger.warning(f"Failed to get collection for notebook {notebook_id}: {e}, falling back to default")
                collection = self.collection

        seen_ids: set = set()
        all_chunks: List[Dict] = []

        for emb in embeddings:
            kwargs: Dict[str, Any] = {
                "query_embeddings": [emb.tolist()],
                "n_results": k,
            }
            if filter_metadata:
                kwargs["where"] = filter_metadata

            try:
                results = collection.query(**kwargs)
            except Exception as e:
                logger.warning(f"Query variant failed: {e}")
                continue

            if not results["ids"][0]:
                continue

            for i in range(len(results["ids"][0])):
                cid = results["ids"][0][i]
                if cid in seen_ids:
                    continue
                seen_ids.add(cid)
                score = round(1 - results["distances"][0][i], 4)
                all_chunks.append({
                    "chunk_id":        cid,
                    "text":            results["documents"][0][i],
                    "metadata":        results["metadatas"][0][i],
                    "relevance_score": score,
                    "doc_id":          results["metadatas"][0][i].get("doc_id", "unknown"),
                })

        all_chunks.sort(key=lambda x: x["relevance_score"], reverse=True)
        top = all_chunks[:k]
        logger.info(f"Multi-query: {len(all_chunks)} unique chunks → returning top {len(top)}")
        return top

    def get_document_chunks(self, doc_id: str, notebook_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch every chunk belonging to one document, in reading order
        (by chunk_index). Used for whole-document summary requests — a
        top-K similarity search against a generic "summarize this" query
        can easily miss entire sections of a long document, since nothing
        in the query embedding points at any particular part of it. When
        the user clearly means "the whole document", read the whole thing.
        """
        collection = self.collection
        if notebook_id and notebook_id != self.notebook_id:
            try:
                collection = self.chroma_client.get_or_create_collection(
                    name=f"documents_{notebook_id}",
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception:
                collection = self.collection

        try:
            results = collection.get(where={"doc_id": doc_id})
        except Exception as e:
            logger.warning(f"get_document_chunks failed for {doc_id}: {e}")
            return []

        if not results.get("ids"):
            return []

        chunks = [
            {
                "chunk_id": cid,
                "text": results["documents"][i],
                "metadata": results["metadatas"][i],
                "relevance_score": 1.0,  # whole-document mode — every chunk is "in scope"
                "doc_id": results["metadatas"][i].get("doc_id", doc_id),
            }
            for i, cid in enumerate(results["ids"])
        ]
        chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
        return chunks

    # ── Remove / Stats ─────────────────────────────────────────────────────

    def remove_document(self, doc_id: str) -> int:
        try:
            results = self.collection.get(where={"doc_id": doc_id})
            if results["ids"]:
                self.collection.delete(ids=results["ids"])
                logger.info(f"Removed {len(results['ids'])} chunks for {doc_id}")
                return len(results["ids"])
            return 0
        except Exception as e:
            logger.error(f"Failed to remove document {doc_id}: {e}")
            return 0

    def get_collection_stats(self) -> Dict[str, Any]:
        try:
            count = self.collection.count()
            all_results = self.collection.get()
            unique_docs = {m.get("doc_id", "unknown")
                          for m in (all_results.get("metadatas") or [])}
            return {"total_chunks": count, "unique_documents": len(unique_docs),
                    "collection_name": self.collection.name}
        except Exception as e:
            logger.error(f"get_collection_stats failed: {e}")
            return {"error": str(e)}

    # ── Format for prompt ──────────────────────────────────────────────────

    def format_context_for_prompt(
        self,
        context_chunks: List[Dict[str, Any]],
        max_length: int = 8000,
    ) -> str:
        """
        Assemble retrieved chunks into a prompt-ready context block.

        Groups chunks by source document so the LLM sees coherent
        context rather than a random interleaving of fragments.
        """
        if not context_chunks:
            return ""

        # Group by doc_id
        from collections import defaultdict
        doc_groups: Dict[str, List] = defaultdict(list)
        for chunk in context_chunks:
            doc_groups[chunk["doc_id"]].append(chunk)

        parts: List[str] = []
        total = 0

        for doc_id, chunks in doc_groups.items():
            filename = chunks[0]["metadata"].get("filename", doc_id)
            header = f"### Source: {filename}"
            block_lines = [header]
            for chunk in chunks:
                text = chunk["text"].strip()
                score = chunk["relevance_score"]
                block_lines.append(f"[relevance: {score:.2f}]\n{text}")
            block = "\n\n".join(block_lines) + "\n"
            if total + len(block) > max_length:
                break
            parts.append(block)
            total += len(block)

        return "\n\n---\n\n".join(parts)