# main.py
import os
import json
import re
import logging
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from urllib.parse import quote
from dotenv import load_dotenv

# ⚠️ CRITICAL: Load .env BEFORE any other imports that use os.getenv()
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from powerbi import router as powerbi_router
from database import init_db as init_powerbi_db

from database_manager import DatabaseManager
from sql_generator import SQLGenerator
from analysis_engine import AnalysisEngine
from visualization_engine import VisualizationEngine
from datasources import (
    router as datasources_router, set_active_db_manager, get_active_db_manager,
    _get_nb_sources, _get_nb_engine, _run_schema_generation, bump_and_push,
)
import database
from document_manager import DocumentManager
from rag_engine import RAGEngine, get_rag_engine
import user_store
import auth as auth_module

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SQL Agent API",
    description="Standalone SQL Analytics Agent",
    version="1.0.0"
)


@app.on_event("startup")
async def on_startup():
    user_store.init_db()
    user_store.purge_expired_sessions()
    init_powerbi_db()
    from hydration import hydrate_all_users
    await hydrate_all_users()

cors_origins = os.getenv("CORS_ORIGINS", "https://cai-demo.circulants.ai,http://cai-demo.circulants.ai").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasources_router)
app.include_router(powerbi_router, prefix="/api/powerbi", tags=["PowerBI"])

# ── Auth Endpoints ─────────────────────────────────────────────────────────────

A2_URL     = os.getenv("A2_URL", "https://a2.circulants.ai")
CAI_URL    = os.getenv("CAI_URL", "https://cai-demo.circulants.ai")
FRONTEND_URL = os.getenv("FRONTEND_URL", CAI_URL)


@app.get("/auth/sso")
async def sso_callback(token: str, request: Request, redirect: str = "/"):
    """Validate the short-lived JWT from a2 and create a CAI session."""
    payload = auth_module.validate_sso_token(token)
    if not payload:
        return RedirectResponse(url=f"{A2_URL}/auth/token?redirect={CAI_URL}/auth/sso")
    user = user_store.upsert_user(
        email=payload.get("email", ""),
        first_name=payload.get("firstName", ""),
        last_name=payload.get("lastName", ""),
    )
    session_token = user_store.create_session(user["id"])
    # Restore this user's data sources into memory if not already loaded
    from datasources import _get_user_sources
    if not _get_user_sources(user["id"]):
        from hydration import hydrate_user
        await hydrate_user(user["id"])
    # Send the user back to wherever they originally tried to go (e.g. a
    # /share/{token} link) instead of always landing on the site root —
    # `redirect` was threaded through /auth/login → a2 → back here.
    dest = redirect if redirect.startswith("/") else "/"
    response = RedirectResponse(url=f"{FRONTEND_URL}{dest}")
    auth_module.set_session_cookie(response, session_token)
    logger.info("SSO login: %s", user["email"])
    return response


@app.get("/auth/login")
async def login_redirect(redirect: str = "/"):
    """No session — send user through a2's token bridge so session is created on both sides."""
    # Carry the originally-requested path through the whole a2 round-trip so
    # /auth/sso can send the user back to it (e.g. a shared-chat link) rather
    # than always landing on the site root. Only the inner path is
    # percent-encoded; the outer a2 handoff URL is left in the same raw form
    # it already worked in.
    safe_redirect = redirect if redirect.startswith("/") else "/"
    callback = f"{CAI_URL}/auth/sso?redirect={quote(safe_redirect, safe='')}"
    return RedirectResponse(url=f"{A2_URL}/auth/token?redirect={callback}")

@app.get("/auth/dev-login")
async def dev_login(request: Request, redirect: str = "/", email: str = "dev@local.com", name: str = "Dev User"):
    """
    Local dev only — bypasses SSO. Requires ALLOW_DEV_LOGIN=true.

    Accepts an optional ?email= (and ?name=) so you can log in as a second
    fake identity in another browser/incognito window — e.g. to verify a
    shared chat link only exposes that one chat to a *different* account,
    not just to the same session that created it. Defaults stay the same as
    before, so existing dev-login links are unaffected.
    """
    if os.getenv("ALLOW_DEV_LOGIN") != "true":
        raise HTTPException(status_code=404, detail="Not found")

    first_name, _, last_name = name.partition(" ")

    # upsert the user
    user_store.upsert_user(
        email=email,
        first_name=first_name or "Dev",
        last_name=last_name or "User",
    )
    # fetch separately after commit
    import hashlib
    uid = hashlib.sha256(email.lower().encode()).hexdigest()[:32]
    user = user_store.get_user_by_id(uid)
    if not user:
        raise HTTPException(status_code=500, detail="Failed to create dev user")
    
    session_token = user_store.create_session(user["id"])
    from hydration import hydrate_user
    await hydrate_user(user["id"])
    dest = redirect if redirect.startswith("/") else "/"
    response = RedirectResponse(url=f"{FRONTEND_URL}{dest}")
    auth_module.set_session_cookie(response, session_token)
    logger.info("Dev login used")
    return response

@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the current user or 401."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {
        "user_id":    user["user_id"],
        "email":      user["email"],
        "first_name": user["first_name"],
        "last_name":  user["last_name"],
        "persona":    user.get("persona"),
    }


@app.post("/auth/logout")
async def logout(request: Request):
    user = auth_module.get_optional_user(request)
    if user:
        user_store.delete_session(user["token"])
        logger.info("Logout: %s", user["email"])
    response = JSONResponse({"success": True})
    auth_module.clear_session_cookie(response)
    return response


@app.patch("/api/user/persona")
async def update_persona(request: Request):
    """Update the user's work persona."""
    user = auth_module.get_current_user(request)
    body = await request.json()
    persona = body.get("persona")
    valid_personas = ["executive", "sales_manager", "field_rep", "analyst"]
    if not persona or persona not in valid_personas:
        raise HTTPException(status_code=400, detail=f"Invalid persona. Must be one of: {', '.join(valid_personas)}")
    updated_user = user_store.update_user_persona(user["user_id"], persona)
    logger.info("Updated persona for %s: %s", user["email"], persona)
    return {
        "user_id":    updated_user["id"],
        "email":      updated_user["email"],
        "first_name": updated_user["first_name"],
        "last_name":  updated_user["last_name"],
        "persona":    updated_user["persona"],
    }


# ── Chat / Session Endpoints ───────────────────────────────────────────────────

@app.get("/api/chats")
async def list_chats(request: Request):
    user = auth_module.get_current_user(request)
    return {"chats": user_store.get_user_chats(user["user_id"])}


@app.post("/api/chats")
async def create_chat(request: Request):
    user = auth_module.get_current_user(request)
    try:
        body = await request.json()
    except:
        body = {}
    import uuid
    cid   = body.get("id") or str(uuid.uuid4())
    title = body.get("title", "New Chat")
    user_store.create_chat(user["user_id"], cid, title)
    return {"id": cid, "title": title}


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, request: Request):
    auth_module.get_current_user(request)
    return {"messages": user_store.get_chat_messages(chat_id)}


@app.patch("/api/chats/{chat_id}")
async def update_chat(chat_id: str, request: Request):
    auth_module.get_current_user(request)
    body = await request.json()
    if "title" in body:
        user_store.update_chat_title(chat_id, body["title"])
    return {"success": True}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request):
    user = auth_module.get_current_user(request)
    user_store.delete_chat(chat_id, user["user_id"])
    return {"success": True}


@app.post("/api/chats/{chat_id}/messages")
async def save_message(chat_id: str, request: Request):
    auth_module.get_current_user(request)
    body = await request.json()
    fields = body.copy()
    fields.pop("id", None)
    fields.pop("role", None)
    user_store.save_message(chat_id=chat_id, msg_id=body["id"], role="assistant", **fields)
    return {"success": True}


# ── Default Database Initialization ───────────────────────────────────────────
_auto_connect = os.getenv("AUTO_CONNECT_DB", "false").lower() == "true"
db_type = os.getenv("DATABASE_TYPE", "sqlite")
db_path = os.getenv("DATABASE_PATH", "")

if _auto_connect and db_type == "sqlite" and db_path:
    if not os.path.isabs(db_path):
        backend_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(backend_dir, db_path.lstrip("./"))
    if os.path.exists(db_path):
        logger.info(f"Auto-connecting default database: {db_path}")
        default_db_manager = DatabaseManager(db_path=db_path, db_type="sqlite")
        set_active_db_manager(default_db_manager)
    else:
        logger.info(f"DATABASE_PATH '{db_path}' not found — skipping auto-connect")
else:
    logger.info("Auto-connect disabled — waiting for UI connection")

openai_api_key = os.getenv("OPENAI_API_KEY")
analysis_engine = AnalysisEngine(api_key=openai_api_key)
visualization_engine = VisualizationEngine()

# Initialize Document Manager (no rule extraction)
document_manager = DocumentManager()

# Initialize RAG Engine — uses cached instance via get_rag_engine()
rag_engine = get_rag_engine()

# Thread pool for parallel SQL + RAG execution
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="query-worker")

logger.info("SQL Agent initialized — SQL and RAG pipelines are independent")

# ── Pydantic Models ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    previous_question: Optional[str] = None
    previous_sql: Optional[str] = None
    previous_summary: Optional[str] = None
    source_filter: Optional[str] = "all"  # Now supports comma-separated values like "database,documents"
    # Per-source exclusion — IDs of individual connected sources (database
    # aliases, document doc_ids, dashboard ids as strings) that the user has
    # unchecked in the Sources panel, on top of the category-level
    # source_filter above. Both apply together: a source is only queried if
    # its category is enabled AND its own id isn't in this list.
    excluded_source_ids: Optional[List[str]] = []
    notebook_id: Optional[str] = None
    chat_id: Optional[str] = None  # When set, assistant response is persisted to this chat
    conversation_history: Optional[List[Dict[str, Any]]] = []  # Full chat history for context
    # Dashboard page context (from Power BI JS SDK)
    page_name: Optional[str] = None
    active_filters: Optional[List[Dict[str, Any]]] = None
    page_visual_data: Optional[List[Dict[str, Any]]] = None
    page_visual_inventory: Optional[List[Dict[str, Any]]] = None  # NEW — full unfiltered {title, type} per visual

class SQLResponse(BaseModel):
    success: bool
    sql_query: Optional[str]
    error: Optional[str]

class CompleteResponse(BaseModel):
    success: bool
    sql_query: Optional[str]
    data: Optional[List[Dict[str, Any]]]
    columns: Optional[List[str]]
    summary: Optional[str]
    follow_up_questions: Optional[List[str]]
    chart_data: Optional[Dict[str, Any]]
    chart_type: Optional[str]
    error: Optional[str]
    referenced_documents: Optional[List[Dict[str, Any]]] = None
    # Which source(s) actually produced this answer — e.g. "dashboard",
    # "database", "documents", or "dashboard,database" for a combined
    # answer. The frontend (ChatPanel.tsx) uses this to decide which
    # preview panel tab to switch to after a response comes back; it was
    # already reading res.answered_by, but nothing on this side ever set
    # it, so the "stay on Dashboard tab" branch there was silently dead
    # code for every dashboard answer, credentialed or pbix-imported.
    answered_by: Optional[str] = None


# ── Helper: format referenced docs for response ────────────────────────────────

def _format_referenced_documents(retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "doc_id": chunk["doc_id"],
            "filename": chunk["metadata"].get("filename", "Unknown"),
            "category": chunk["metadata"].get("category", "general"),
            "page": chunk["metadata"].get("page"),
            "relevance_score": round(chunk["relevance_score"], 3),
            "excerpt": chunk["text"][:200] + "..." if len(chunk["text"]) > 200 else chunk["text"],
        }
        for chunk in retrieved_docs
    ]


def _filter_excluded_chunks(
    chunks: Optional[List[Dict[str, Any]]],
    excluded_ids: set,
) -> Optional[List[Dict[str, Any]]]:
    """
    Drops any retrieved chunk whose doc_id is in excluded_ids — i.e. the
    user unchecked that specific document in the Sources panel. Applied
    AFTER retrieval rather than passed into rag_engine's query itself,
    since the vector search doesn't need to know about this; it's just a
    presence/absence filter on results that are already scored and ranked.
    A no-op (returns chunks unchanged) when excluded_ids is empty.
    """
    if not chunks or not excluded_ids:
        return chunks
    return [c for c in chunks if c.get("doc_id") not in excluded_ids]


def _build_referenced_documents(
    retrieved_docs: Optional[List[Dict[str, Any]]],
    used_chunk_ids: Optional[List[str]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Citations should reflect what the model actually drew on to write the
    answer, not everything that happened to be retrieved — a chunk can be
    fetched (especially in a multi-document notebook) and simply not be
    what the model used. The model reports which CHUNK_IDs it actually used
    (see generate_document_response's "used_chunk_ids"); filter down to
    those. Falls back to showing everything retrieved if that field is
    missing (older/fallback parse path) or ends up matching nothing at all,
    rather than leaving the user with zero citations for a real answer.
    """
    if not retrieved_docs:
        return None
    if used_chunk_ids is None:
        return _format_referenced_documents(retrieved_docs)
    used_set = set(used_chunk_ids)
    filtered = [d for d in retrieved_docs if d.get("chunk_id") in used_set]
    if not filtered:
        return _format_referenced_documents(retrieved_docs)
    return _format_referenced_documents(filtered)


def _render_summary(result: Dict[str, Any]) -> Optional[str]:
    """
    Turn a generator's structured result into final markdown text.

    The model itself decides format when it generates the answer (it reads
    the question directly — no external guessing needed). When it chooses
    list format, "summary" comes back as an array of plain point strings;
    this just joins them with "- " deterministically, so the bullets are
    always correct regardless of the model's own text habits. When it
    chooses prose, "summary" is already the final string.
    """
    summary = result.get("summary") if result else None
    if isinstance(summary, list):
        return "\n".join(f"- {item}" for item in summary if str(item).strip())
    return summary


# ── Documents-only path ────────────────────────────────────────────────────────

def _is_dashboard_referential(question: str) -> bool:
    """
    True when the question is clearly asking about the DASHBOARD/report/page
    itself (its title, structure, visuals, pages) rather than about an
    uploaded document. Generic phrasing like "summarize" or "tell me about"
    on its own says nothing about which source is meant — this catches the
    cases where the wording explicitly points at the dashboard so that:
      1. the combined "all sources" query can skip documents/database
         instead of firing them on every dashboard question, and
      2. whole-document RAG mode (which bypasses the relevance floor) never
         gets triggered by dashboard wording, even when a notebook happens
         to have exactly one document connected (see _resolve_document_scope).
    """
    patterns = [
        r'\b(this|my|the) (dashboard|report)\b',
        r'\bdashboard\'?s (title|name)\b',
        r'\bhow many (visuals?|charts?|widgets?|pages?|tables?|measures?)\b',
        r'\bnumber of (visuals?|charts?|widgets?|pages?|tables?|measures?)\b',
        r'\btitles? of (the |all )?visuals?\b',
        r'\b(this|the) page\b',
        r'\bpage\'?s (title|name)\b',
        r'\bwhat (visuals?|charts?) (are|is) on\b',
        r'\blist (all )?(the )?(visuals?|pages?)\b',
    ]
    q = question.lower()
    return any(re.search(p, q) for p in patterns)


def _is_summary_query(question: str) -> bool:
    """Detect if the user wants a full document summary vs a specific data lookup."""
    summary_keywords = ["summarize", "summarise", "summary", "overview", "brief",
                        "key points", "what does", "what is in", "tell me about",
                        "explain", "describe", "highlight", "main points", "key findings",
                        "what are the", "give me a", "walk me through"]
    q = question.lower()
    return any(kw in q for kw in summary_keywords)


MAX_WHOLE_DOC_CHUNKS = 40


def _sample_evenly(items: list, cap: int) -> list:
    """Even coverage across a long document instead of just its first N
    chunks, if it has more chunks than we want to send to the model."""
    if len(items) <= cap:
        return items
    step = len(items) / cap
    return [items[int(i * step)] for i in range(cap)]


def _resolve_document_scope(question: str, notebook_id: Optional[str]) -> Optional[str]:
    """
    Returns a doc_id to scope retrieval to, or None to search the whole
    notebook. Two cases:
    - Exactly one document connected: scope to it — nothing to disambiguate.
    - Multiple documents connected: only scope to one if the question
      clearly names it via a distinctive word from its own filename (e.g.
      "summarise the resume" naming "Resume_Sankalp_Gaur.pdf"). Otherwise
      return None and let retrieval search across all of them.

    This is what stops an unrelated document's chunks from bleeding into an
    answer just because they happened to score high enough in a notebook-
    wide similarity search — a real risk once a notebook has 2+ documents,
    since a generic query like "summarize" or "the resume" has nothing in
    its embedding that inherently excludes other documents.
    """
    if not notebook_id:
        return None
    try:
        docs = document_manager.list_documents(notebook_id=notebook_id)
    except Exception:
        return None
    if not docs:
        return None
    if len(docs) == 1:
        return docs[0]["doc_id"]

    q = question.lower()
    q_squashed = re.sub(r'\s+', '', q)  # handles concatenated filenames like "genetherapy" vs "gene therapy"

    scored = []
    for d in docs:
        filename = d.get("filename", "") or ""
        stem = re.sub(r'\.[a-zA-Z0-9]+$', '', filename)
        words = [w for w in re.split(r'[_\-\s]+', stem.lower()) if len(w) > 2]
        hits = [w for w in words if w in q or w in q_squashed]
        if hits and words:
            scored.append((len(hits) / len(words), len(hits), d["doc_id"]))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top_ratio, top_hits, top_doc_id = scored[0]

    # A single but genuinely distinctive word match is enough on its own —
    # most real filenames only have 1-3 meaningful words to begin with, so
    # requiring 2+ hits or a 50%+ ratio would almost never fire in practice.
    # Just make sure it's a clear win over any runner-up document, not a tie.
    clear_winner = len(scored) == 1 or top_ratio > scored[1][0] + 0.15 or top_hits > scored[1][1]
    return top_doc_id if clear_winner else None


def _get_whole_document_chunks(question: str, notebook_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """
    For a genuine "summarize this document" request, a top-K similarity
    search against the generic word "summarize" can easily miss whole
    sections of a real document — nothing in that query embedding points at
    any specific part of it. Read the whole thing instead of guessing which
    chunks matter, but only once we know WHICH document — see
    _resolve_document_scope. Returns None if that's ambiguous, so callers
    fall back to normal similarity retrieval.

    Guarded against dashboard-referential wording ("summarize this
    dashboard", "how many visuals on this page") — that phrasing also trips
    _is_summary_query's generic keyword list, and _resolve_document_scope
    will happily auto-scope to a notebook's sole connected document even
    though the question was never about that document. Bailing out here
    forces the normal similarity-search path instead, which — unlike whole-
    document mode — still enforces MIN_RELEVANCE before answering.
    """
    if _is_dashboard_referential(question):
        return None
    doc_id = _resolve_document_scope(question, notebook_id)
    if not doc_id:
        return None
    try:
        chunks = rag_engine.get_document_chunks(doc_id, notebook_id=notebook_id)
    except Exception:
        return None
    if not chunks:
        return None
    return _sample_evenly(chunks, MAX_WHOLE_DOC_CHUNKS)


def _process_document_only(request: QueryRequest, persona: str = None) -> CompleteResponse:
    excluded_ids = set(request.excluded_source_ids or [])
    is_summary = _is_summary_query(request.question)
    top_k = 20 if is_summary else 10

    retrieved_docs = _get_whole_document_chunks(request.question, request.notebook_id) if is_summary else None
    if retrieved_docs is None:
        # Fold the previous question in whenever there is one — simple and
        # unconditional. A short follow-up ("give me 7 points") has no topic of
        # its own, so this lets retrieval find the right chunks; for a
        # self-contained question it's just harmless extra context alongside
        # the question's own words, which the embedding search already weighs.
        retrieval_query = request.question
        if request.previous_question:
            retrieval_query = f"{request.question} {request.previous_question}"

        # Even for a non-summary lookup, scope to a specific document if the
        # question clearly names one — otherwise an unrelated document's
        # chunks can outscore the right one in a notebook-wide search just
        # by coincidental wording overlap.
        scoped_doc_id = _resolve_document_scope(request.question, request.notebook_id)
        filter_metadata = {"doc_id": scoped_doc_id} if scoped_doc_id else None

        retrieved_docs = rag_engine.retrieve_context(
            query=retrieval_query, top_k=top_k, notebook_id=request.notebook_id,
            filter_metadata=filter_metadata,
        )

    retrieved_docs = _filter_excluded_chunks(retrieved_docs, excluded_ids)

    analysis_result = analysis_engine.generate_document_response(
        question=request.question,
        context_chunks=retrieved_docs,
        previous_question=request.previous_question,
        previous_summary=request.previous_summary,
        persona=persona,
        is_summary=is_summary,
        conversation_history=request.conversation_history or [],
    )
    referenced_documents = _build_referenced_documents(retrieved_docs, analysis_result.get("used_chunk_ids"))
    return CompleteResponse(
        success=True,
        sql_query=None,
        data=None,
        columns=None,
        summary=_render_summary(analysis_result),
        follow_up_questions=analysis_result.get("follow_up_questions"),
        chart_data=None,
        chart_type="none",
        error=None,
        referenced_documents=referenced_documents,
        answered_by="documents",
    )


# ── Database-only path ─────────────────────────────────────────────────────────

async def _process_database_only(request: QueryRequest, db_manager, persona: str = None) -> CompleteResponse:
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_executor, _run_sql_sync, request, db_manager, persona)

    if not result["success"]:
        if result.get("out_of_scope"):
            return CompleteResponse(
                success=False, error=None,
                sql_query=None, data=None, columns=None,
                summary=result.get("error"), follow_up_questions=None,
                chart_data=None, chart_type="none",
            )
        return CompleteResponse(
            success=False, error=result.get("error"),
            sql_query=None, data=None, columns=None,
            summary=None, follow_up_questions=None,
            chart_data=None, chart_type=None,
        )

    return CompleteResponse(
        success=True,
        sql_query=result["sql_query"],
        data=result["data_records"],
        columns=result["columns"],
        summary=result["summary"],
        follow_up_questions=result["follow_up_questions"],
        chart_data=result["chart_data"],
        chart_type=result["chart_type"],
        error=None,
        answered_by="database",
        referenced_documents=None,
    )


# ── LLM Router ────────────────────────────────────────────────────────────────

# ── Multi-source processor ────────────────────────────────────────────────────

async def _process_routed(
    request: QueryRequest,
    db_manager,
    user_id: str,
    persona: str = None,
) -> CompleteResponse:
    """
    Always hits all connected sources in parallel.
    One LLM call at the end looks at every result and writes the answer,
    stressing whichever source gave the most accurate/relevant response.
    If no source returned anything useful, returns success=False — no forced answer.
    """
    loop = asyncio.get_event_loop()

    # ── Resolve notebook-scoped dashboard ─────────────────────────────────
    excluded_ids = set(request.excluded_source_ids or [])
    connected_dashboard = None
    if user_id:
        try:
            from database import get_dashboards_for_notebook, get_dashboards_for_user
            nb_id = request.notebook_id
            dashboards = (
                get_dashboards_for_notebook(nb_id, user_id)
                if nb_id else get_dashboards_for_user(user_id)
            )
            dashboards = [d for d in dashboards if str(d.get('id')) not in excluded_ids]
            connected_dashboard = next((d for d in dashboards if d["type"] == "powerbi"), None)
        except Exception as e:
            logger.warning(f"Dashboard lookup failed: {e}")

    # ── Launch selected sources in parallel ───────────────────────────
    # Parse source_filter to determine which sources to query
    sources_to_query = set(s.strip() for s in request.source_filter.split(',') if s.strip())
    was_unfiltered = not sources_to_query or 'all' in sources_to_query
    if was_unfiltered:
        sources_to_query = {'dashboard', 'database', 'documents'}

    # A question that's explicitly about the dashboard itself ("how many
    # visuals on this page", "what's this dashboard's title") has no business
    # triggering document/database lookups — those sources have nothing to
    # do with the question, and (as seen with whole-document RAG mode) can
    # confidently return unrelated content that then gets stitched into the
    # answer next to the real dashboard response. Only apply this narrowing
    # when the person didn't explicitly ask for "all" sources by name filter
    # — i.e. leave explicit multi-source picks alone.
    if was_unfiltered and connected_dashboard and _is_dashboard_referential(request.question):
        sources_to_query = {'dashboard'}

    futures = {}

    if connected_dashboard and 'dashboard' in sources_to_query:
        futures["dashboard"] = loop.run_in_executor(
            _executor, _run_dashboard_sync_nb, request, connected_dashboard
        )

    if db_manager and 'database' in sources_to_query:
        futures["database"] = loop.run_in_executor(
            _executor, _run_sql_sync, request, db_manager, persona
        )

    if 'documents' in sources_to_query:
        futures["documents"] = loop.run_in_executor(
            _executor, _run_rag_sync, request, persona
        )

    # ── Gather results ─────────────────────────────────────────────────────
    keys = list(futures.keys())
    results_raw = await asyncio.gather(*futures.values(), return_exceptions=True)
    results = {}
    for k, r in zip(keys, results_raw):
        if isinstance(r, Exception):
            logger.error(f"Source '%s' raised: %s", k, r)
            results[k] = {"success": False}
        else:
            results[k] = r

    # ── Unpack outputs — only sources that actually returned content ───────
    sql_query    = None
    data_records = None
    columns      = None
    chart_data   = None
    chart_type   = "none"
    referenced_documents = None
    source_outputs = {}   # source -> summary string, only populated if non-empty

    db_result = results.get("database", {})
    if db_result.get("success") and db_result.get("summary"):
        sql_query    = db_result.get("sql_query")
        data_records = db_result.get("data_records")
        columns      = db_result.get("columns")
        chart_data   = db_result.get("chart_data")
        chart_type   = db_result.get("chart_type", "none")
        source_outputs["database"] = db_result["summary"]

    dash_result = results.get("dashboard", {})
    if dash_result.get("success") and dash_result.get("summary"):
        source_outputs["dashboard"] = dash_result["summary"]
        if not sql_query:
            sql_query = dash_result.get("dax_query")
        if not data_records:
            data_records = dash_result.get("rows")

    rag_result = results.get("documents", {})
    if rag_result.get("success") and rag_result.get("summary"):
        source_outputs["documents"] = rag_result["summary"]
        retrieved_docs = rag_result.get("retrieved_docs", [])
        referenced_documents = _build_referenced_documents(retrieved_docs, rag_result.get("used_chunk_ids"))

    # ── Nothing came back — don't force an answer ─────────────────────────
    if not source_outputs:
        logger.info("All sources returned empty — no answer generated")
        return CompleteResponse(
            success=False,
            error="No results found across available sources for this question.",
            sql_query=None, data=None, columns=None,
            summary=None, follow_up_questions=None,
            chart_data=None, chart_type=None,
        )

    # ── One LLM call — always synthesise across all connected sources ───────
    # Even if only one source returned data, the synthesis shows every source
    # (including the ones that returned nothing) so the user always sees the
    # full picture of what was queried.
    all_follow_ups = []
    for k in keys:
        all_follow_ups += results.get(k, {}).get("follow_up_questions", [])
    follow_up_questions = list(dict.fromkeys(all_follow_ups))[:6]

    summary = await loop.run_in_executor(
        _executor, _synthesise_results,
        request.question, source_outputs, keys
    )

    return CompleteResponse(
        success=True,
        sql_query=sql_query,
        data=data_records,
        columns=columns,
        summary=summary,
        follow_up_questions=follow_up_questions,
        chart_data=chart_data,
        chart_type=chart_type,
        error=None,
        referenced_documents=referenced_documents,
        answered_by=",".join(source_outputs.keys()),
    )


# ── Dashboard sync worker (notebook-aware) ────────────────────────────────────

def _run_dashboard_sync_nb(request: QueryRequest, dashboard: dict) -> dict:
    """Sync dashboard worker that takes the already-resolved dashboard dict
    (notebook-scoped) rather than doing its own user-level lookup."""
    try:
        import powerbi_service
        result = powerbi_service.run_chat(
            creds=dashboard["config"],
            dashboard_id=dashboard["id"],
            message=request.question,
            page_name=request.page_name,
            active_filters=request.active_filters,
            page_visual_data=request.page_visual_data,
            page_visual_inventory=request.page_visual_inventory,
            conversation_history=request.conversation_history or [],
        )
        insight = result.get("insight", "")
        status  = result.get("status")

        # "no_results" (including meta answers like "I couldn't read the
        # visuals on this page") still has something worth telling the user —
        # it just lives in "message" instead of "insight". Only collapse to
        # a silent failure when there's truly nothing to show.
        if status in ("out_of_scope", "error") or (not insight and not result.get("message")):
            return {"success": False}
        if status == "no_results" and not insight:
            message = result.get("message")
            if not message:
                return {"success": False}
            return {"success": True, "summary": message}

        return {
            "success":             True,
            "summary":             insight,
            "dax_query":           result.get("dax_query"),
            "rows":                result.get("rows", []),
            "follow_up_questions": result.get("follow_up_questions", []),
        }
    except Exception as e:
        logger.error(f"_run_dashboard_sync_nb failed: {e}", exc_info=True)
        return {"success": False}


# ── Multi-source synthesis ─────────────────────────────────────────────────────

_LEADING_SOURCE_HEADING_RE = re.compile(
    r'^\s*\*\*\s*(Power BI Dashboard|Database|Documents)\s*:?\s*\*\*\s*\n*',
    re.IGNORECASE,
)
_TRAILING_SUMMARY_SECTION_RE = re.compile(
    r'\n+\*\*\s*Summary\s*:?\s*\*\*.*\Z',
    re.IGNORECASE | re.DOTALL,
)


def _clean_single_source_answer(text: str) -> str:
    """
    Plain text cleanup (no LLM call) for the single-source case: strips a
    stray per-source heading (e.g. "**Documents:**") or trailing
    "**Summary:**" section if the source's own generator happened to
    include one. Deterministic, so it can never alter the actual formatting
    (bullets, numbering, bold) the generation call already produced.
    """
    if not text:
        return text
    cleaned = _LEADING_SOURCE_HEADING_RE.sub('', text.strip())
    cleaned = _TRAILING_SUMMARY_SECTION_RE.sub('', cleaned)
    return cleaned.strip()


def _synthesise_results(question: str, source_outputs: dict, connected_sources: list) -> str:
    """
    Only surfaces sources that actually returned a relevant answer.
    Sources that came back empty are dropped entirely — no "No results
    returned" filler is ever shown to the user.

    - If exactly one source answered: return that answer directly, with no
      per-source heading and no trailing "Summary:" line.
    - If multiple sources answered: show each under its own bold heading
      (only the ones that answered), followed by a short "Summary:" line
      leading with whichever was most accurate.
    """
    from openai import OpenAI
    client = OpenAI(api_key=openai_api_key)

    source_labels = {
        "dashboard": "Power BI Dashboard",
        "database":  "Database",
        "documents": "Documents",
    }

    # Only keep sources that actually returned something — empty ones are dropped, not labelled
    answered = [(src, source_outputs[src]) for src in connected_sources if source_outputs.get(src)]

    # ── Exactly one source answered — return it directly, no LLM rewrite ──
    # The source's own generator (generate_document_response /
    # generate_combined_analysis / dashboard insight) already received the
    # RESPONSE STYLE instructions and produced correctly formatted output
    # (bullets, bold, brief/elaborate, etc). Running that back through a
    # second "rewrite it cleanly" LLM call was found to sometimes silently
    # drop the list formatting even when re-instructed — an unnecessary
    # extra model call that could only make the answer worse, never better.
    # None of the per-source generators reference other sources, so there's
    # nothing to strip in the normal case; this is just a defensive net for
    # the rare case where a source's own text happens to include a stray
    # heading or trailing "Summary:" section.
    if len(answered) == 1:
        _, summary = answered[0]
        return _clean_single_source_answer(summary)

    # ── Multiple sources answered ──────────────────────────────────────────
    blocks = [f"[{source_labels.get(src, src)}]\n{summary}" for src, summary in answered]
    answered_labels = [source_labels.get(src, src) for src, _ in answered]

    prompt = f"""You received results from multiple connected data sources for a user question.
Only the sources below actually returned a relevant answer — every other connected
source returned nothing useful and has already been excluded; do not mention or
imply the existence of any source not listed here.

USER QUESTION: "{question}"

RESULTS THAT ANSWERED THIS QUESTION:
{"="*50}
{chr(10).join(blocks)}
{"="*50}

The sources that answered are: {", ".join(answered_labels)}.

First, check: does the user's question clearly and explicitly name ONE specific source
only — e.g. "the dashboard"/"Power BI", "the database"/"the table(s)"/"SQL", or "the
document(s)"/"the file(s)"/"the report" (as an uploaded document, not the dashboard)?

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "briefly", "elaborate" all mean
something plain and obvious, don't guess from keyword lists).

Return ONLY a JSON object:

{{
  "single_source_override": true or false — true ONLY if the question names one
    specific source above and you should answer using ONLY that source,
  "sections": [
    {{"label": "<one of: {", ".join(answered_labels)}>", "format": "list" or "prose",
      "content": <JSON array of point strings if format is "list" (no dashes/numbers —
      the app adds those), otherwise a single markdown string>}}
    // one entry per source that answered — omit entirely if single_source_override is true
    // and just put that one source's answer directly under "override_content" instead
  ],
  "override_content": <same shape as a section's "content", used ONLY if single_source_override is true>,
  "override_format": "list" or "prose" — used ONLY if single_source_override is true,
  "closing_summary_format": "list" or "prose" or "none",
  "closing_summary": <2-3 sentences (or list) leading with the most accurate answer,
    noting any conflicts between sources — use "none"/null if single_source_override is true
    or if there's nothing worth adding beyond the sections above>
}}

Rules:
- Use **bold** within content strings for key numbers/terms
- Do not invent any numbers or facts not present in the results above
- Never say a source "returned nothing" — sources that returned nothing are not listed above and must not be discussed"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a precise analyst. Report exactly what each source returned. Never invent data, and never reference a source that isn't in the provided results. If the user's question names one specific source, answer using only that source and say nothing about the others."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.1,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        parsed = json.loads(raw)

        if parsed.get("single_source_override"):
            return _render_summary({"summary": parsed.get("override_content")})

        parts = []
        for section in parsed.get("sections", []):
            label = section.get("label", "")
            body = _render_summary({"summary": section.get("content")})
            if body:
                parts.append(f"**{label}:** {body}" if section.get("format") != "list" else f"**{label}:**\n{body}")

        if parsed.get("closing_summary_format") not in (None, "none") and parsed.get("closing_summary"):
            closing = _render_summary({"summary": parsed.get("closing_summary")})
            if closing:
                parts.append(f"**Summary:** {closing}")

        return "\n\n".join(parts) if parts else _clean_single_source_answer(answered[0][1])
    except Exception as e:
        logger.error(f"_synthesise_results failed: {e}")
        parts = [f"**{source_labels.get(src, src)}:** {summary}" for src, summary in answered]
        return "\n\n".join(parts)



# ── Blocking workers (run in thread executor) ──────────────────────────────────

def _run_sql_sync(request: QueryRequest, db_manager, persona: str = None) -> dict:
    """Pure SQL path — no RAG involvement whatsoever."""
    try:
        sql_generator = SQLGenerator(api_key=openai_api_key, db_manager=db_manager)

        sql_result = sql_generator.generate_sql(
            question=request.question,
            previous_question=request.previous_question,
            previous_sql=request.previous_sql,
            persona=persona,
            conversation_history=request.conversation_history or [],
        )

        if not sql_result["success"]:
            return {
                "success": False,
                "error": sql_result.get("error"),
                "out_of_scope": sql_result.get("out_of_scope", False),
            }

        # Schema meta-question (e.g. "how many columns", "what is this
        # column about") — answered directly from schema_info, no SQL was
        # run. Return it as a plain summary; everything else below (which
        # assumes an actual result dataframe) is untouched for every other
        # question.
        if sql_result.get("meta_answer"):
            return {
                "success":            True,
                "sql_query":          None,
                "df":                 None,
                "data_records":       None,
                "columns":            None,
                "chart_data":         None,
                "chart_type":         "none",
                "summary":            sql_result["meta_answer"],
                "follow_up_questions": [],
            }

        sql_query = sql_result["sql_query"]
        df = sql_generator._last_result_df
        if df is None:
            logger.warning("_last_result_df was None — falling back to re-execute")
            df = db_manager.execute_query(sql_query)

        # If query returned no rows, don't generate hallucinated summary
        if df.empty or len(df) == 0:
            return {
                "success": False,
                "error": f"Query returned no results.",
            }

        # Analysis (summary + follow-ups) in one call
        try:
            analysis_result = analysis_engine.generate_combined_analysis(
                question=request.question,
                sql_query=sql_query,
                data=df,
                previous_question=request.previous_question,
                previous_summary=request.previous_summary,
                persona=persona,
            )
            summary         = _render_summary(analysis_result)
            follow_up_questions = analysis_result["follow_up_questions"]
        except Exception as e:
            logger.error(f"Combined analysis failed, using fallback: {e}")
            summary = analysis_engine.generate_summary(
                question=request.question, sql_query=sql_query, data=df,
                previous_question=request.previous_question,
                previous_summary=request.previous_summary,
            )
            follow_up_questions = analysis_engine.generate_follow_up_questions(
                question=request.question, data=df,
                previous_question=request.previous_question,
            )

        # Visualization
        vis_result     = visualization_engine.create_visualization(data=df, question=request.question)
        raw_chart_data = vis_result.get("data") if vis_result else None
        chart_data     = raw_chart_data if isinstance(raw_chart_data, dict) else None
        chart_type     = vis_result.get("type") if vis_result else "none"
        if chart_data is None:
            chart_type = "none"

        # Convert dataframe to records safely
        try:
            data_records = json.loads(df.to_json(orient="records", date_format="iso"))
        except Exception as e:
            logger.warning(f"Failed to serialize dataframe to JSON: {e}, using string representation")
            data_records = df.astype(str).to_dict(orient="records")

        return {
            "success":           True,
            "sql_query":         sql_query,
            "df":                df,
            "data_records":      data_records,
            "columns":           df.columns.tolist(),
            "chart_data":        chart_data,
            "chart_type":        chart_type,
            "summary":           summary,
            "follow_up_questions": follow_up_questions,
        }

    except Exception as e:
        logger.error(f"_run_sql_sync failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _run_rag_sync(request: QueryRequest, persona: str = None) -> dict:
    """Pure RAG path — no SQL involvement whatsoever."""
    try:
        excluded_ids = set(request.excluded_source_ids or [])
        is_summary = _is_summary_query(request.question)
        top_k = 20 if is_summary else 10

        whole_doc = is_summary and _get_whole_document_chunks(request.question, request.notebook_id)
        if whole_doc:
            retrieved_docs = _filter_excluded_chunks(whole_doc, excluded_ids)
            if not retrieved_docs:
                return {"success": False}
        else:
            # Fold the previous question in whenever there is one — simple and
            # unconditional, no classifier needed. A topic-less follow-up ("give
            # me 7 points") has no subject of its own, so this lets retrieval
            # find the right chunks; for a self-contained question it's just
            # harmless extra context alongside the question's own words.
            retrieval_query = request.question
            if request.previous_question:
                retrieval_query = f"{request.question} {request.previous_question}"

            # Scope to a specific document if the question clearly names one —
            # otherwise chunks from an unrelated document can outscore the
            # right one in a notebook-wide search.
            scoped_doc_id = _resolve_document_scope(request.question, request.notebook_id)
            filter_metadata = {"doc_id": scoped_doc_id} if scoped_doc_id else None

            retrieved_docs = rag_engine.retrieve_context(
                query=retrieval_query, top_k=top_k, notebook_id=request.notebook_id,
                filter_metadata=filter_metadata,
            )
            retrieved_docs = _filter_excluded_chunks(retrieved_docs, excluded_ids)
            if not retrieved_docs:
                return {"success": False}

            # A basic relevance floor: if nothing retrieved even loosely matches,
            # don't bother calling the model at all. Deliberately lenient — the
            # model itself is asked below to say "answered: false" if the
            # content genuinely doesn't cover the question, which is a much
            # more reliable judge of relevance than a similarity-score cutoff.
            # Doesn't apply to whole_doc mode above — that's deliberate full
            # coverage, not a similarity match, so there's no score to floor.
            MIN_RELEVANCE = 0.2
            best_score = max((c.get("relevance_score", 0) for c in retrieved_docs), default=0)
            if best_score < MIN_RELEVANCE:
                logger.info(f"RAG best relevance {best_score:.3f} below floor {MIN_RELEVANCE} — no document results")
                return {"success": False}

        analysis_result = analysis_engine.generate_document_response(
            question=(
                f"{request.question}\n\n"
                "Note: Answer only what is covered in the provided document context. "
                "If the question has multiple parts, answer only the parts covered by the documents. "
                "Do not answer data/metric questions — those are handled by other sources."
            ),
            context_chunks=retrieved_docs,
            previous_question=request.previous_question,
            previous_summary=request.previous_summary,
            persona=persona,
            is_summary=is_summary,
            conversation_history=request.conversation_history or [],
        )

        # The model itself already told us whether it actually found an
        # answer in the content ("answered": true/false) — trust that
        # directly instead of pattern-matching its text for refusal phrases.
        if not analysis_result.get("answered", True):
            logger.info("RAG generator reported answered=false — no document results")
            return {"success": False}

        return {
            "success":             True,
            "summary":             _render_summary(analysis_result),
            "follow_up_questions": analysis_result.get("follow_up_questions", []),
            "retrieved_docs":      retrieved_docs,
            "used_chunk_ids":      analysis_result.get("used_chunk_ids"),
        }

    except Exception as e:
        logger.error(f"_run_rag_sync failed: {e}", exc_info=True)
        return {"success": False}



@app.get("/api/notebooks/{notebook_id}/chats")
async def list_notebook_chats(notebook_id: str, request: Request):
    """List all chat sessions for a notebook, newest first."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_store.list_notebook_chats(notebook_id, user["user_id"])


@app.post("/api/notebooks/{notebook_id}/chats")
async def create_notebook_chat(notebook_id: str, request: Request):
    """Create a new chat session scoped to a notebook with immutable persona."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = body.get("title", "New Chat")
    persona = body.get("persona")
    return user_store.create_notebook_chat(user["user_id"], notebook_id, title, persona)


@app.get("/api/notebooks/{notebook_id}/chats/{chat_id}")
async def get_notebook_chat(notebook_id: str, chat_id: str, request: Request):
    """Get a specific chat with persona details."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    chat = user_store.get_notebook_chat_detail(chat_id, user["user_id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.patch("/api/notebooks/{notebook_id}/chats/{chat_id}")
async def update_notebook_chat(notebook_id: str, chat_id: str, request: Request):
    """Update chat (title only). Persona is immutable."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        body = await request.json()
    except Exception:
        body = {}

    if "persona" in body:
        raise HTTPException(
            status_code=409,
            detail="Persona cannot be changed. Create a new chat to use a different persona."
        )

    title = body.get("title")
    if title:
        user_store.update_chat_title(chat_id, title)

    chat = user_store.get_notebook_chat_detail(chat_id, user["user_id"])
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


@app.delete("/api/notebooks/{notebook_id}/chats/{chat_id}")
async def delete_notebook_chat(notebook_id: str, chat_id: str, request: Request):
    """Delete a chat session from a notebook."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_store.delete_chat(chat_id, user["user_id"])
    return {"deleted": True}


# ── Chat sharing ─────────────────────────────────────────────────────────────
#
# Sharing a chat snapshots its messages behind an opaque token. Anyone with an
# authenticated SSO session can open the resulting /share/{token} link, but
# that link only ever unlocks that one frozen chat — never the owner's other
# chats, sources, or notebooks. Creating/revoking a share is owner-only;
# viewing a share by token deliberately is not.

@app.post("/api/notebooks/{notebook_id}/chats/{chat_id}/share")
async def share_notebook_chat(notebook_id: str, chat_id: str, request: Request):
    """Create (or return the existing) read-only share link for a chat."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return user_store.create_chat_share(chat_id, notebook_id, user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/notebooks/{notebook_id}/chats/{chat_id}/share")
async def get_notebook_chat_share(notebook_id: str, chat_id: str, request: Request):
    """Return the active share for a chat, if any — used to show 'already shared' state."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_store.get_active_chat_share(chat_id) or {}


@app.delete("/api/notebooks/{notebook_id}/chats/{chat_id}/share")
async def unshare_notebook_chat(notebook_id: str, chat_id: str, request: Request):
    """Revoke the active share link for a chat. Owner only."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"revoked": user_store.revoke_chat_share(chat_id, user["user_id"])}


@app.get("/api/share/{token}")
async def get_shared_chat(token: str, request: Request):
    """
    Fetch a shared chat snapshot. Requires SSO login like every other route in
    the app, but deliberately does NOT check chat/notebook ownership — any
    authenticated user holding the link can view this one chat, and nothing
    else belonging to the sharer.
    """
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    share = user_store.get_chat_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="This share link is invalid or has been revoked")
    return share


@app.post("/api/share/{token}/open")
async def open_shared_chat(token: str, request: Request):
    """
    Turn a share link into a private, live copy for the viewer: same chat
    history, same connected sources (databases, documents, Power BI
    dashboards) — all forked into a brand-new notebook owned by the viewer,
    so they land on the exact same notebook/chat UI as any other chat and
    can keep asking questions, without ever touching the sharer's original
    notebook, chat, or live session.

    Idempotent per viewer: reopening the same link just returns the fork
    that was already created the first time.
    """
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    viewer_id = user["user_id"]

    share = user_store.get_chat_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="This share link is invalid or has been revoked")

    existing = user_store.get_share_fork(token, viewer_id)
    if existing:
        return {"notebook_id": existing["forked_notebook_id"], "chat_id": existing["forked_chat_id"]}

    import uuid, secrets
    owner_id  = share["owner_id"]
    src_nb_id = share["notebook_id"]
    openai_api_key = os.getenv("OPENAI_API_KEY")

    # 1. New notebook + chat for the viewer, seeded from the share snapshot
    notebook = user_store.create_notebook(
        viewer_id,
        name=share.get('notebook_name') or 'Shared notebook',
        persona=share.get("persona"),
        is_shared=True,
        shared_by=share.get("owner_name"),
    )
    chat = user_store.create_notebook_chat(
        viewer_id, notebook["id"],
        title=share.get("chat_title") or "Shared chat",
        persona=share.get("persona"),
    )

    # 2. Replay the frozen messages so history shows up immediately
    for m in share.get("messages", []):
        try:
            user_store.save_message(
                chat["id"], str(uuid.uuid4()), m.get("role", "assistant"),
                question=m.get("question"), sql_query=m.get("sql_query"), summary=m.get("summary"),
                chart_data=m.get("chart_data"), chart_type=m.get("chart_type"),
                columns=m.get("columns"), data=m.get("data"), error=m.get("error"),
                source_filter=m.get("source_filter"), follow_up_questions=m.get("follow_up_questions"),
                out_of_scope=m.get("out_of_scope"),
            )
        except Exception as e:
            logger.warning(f"share fork: failed to copy a message: {e}")

    # 3. Fork database sources — copy connection configs under a fresh alias
    #    (user_data_sources has a UNIQUE(user_id, alias) constraint, so we
    #    can't just reuse the owner's alias name — the viewer may already
    #    have their own source called the same thing), then re-hydrate live
    #    managers and kick off schema generation exactly like a fresh connect.
    try:
        from hydration import _create_manager
        src_rows = user_store.get_user_data_sources(owner_id, notebook_id=src_nb_id)
        for row in src_rows:
            new_alias = f"{row['alias']}_{secrets.token_hex(3)}"
            manager = _create_manager(row["db_type"], new_alias, row["connection_config"], viewer_id)
            if manager is None:
                continue
            selected = row["selected_tables"] or []

            # Copy the already-generated table/column descriptions from the
            # owner's cache entry to the viewer's new one BEFORE reloading
            # schema_info, so set_selected_tables() sees them as already
            # described and skips calling the LLM again — the schema itself
            # (columns, descriptions) is identical since it's the same
            # underlying database, only the alias/schema_alias differ.
            owner_schema_alias = f"{owner_id[:8]}_{row['alias']}"
            new_schema_alias    = f"{viewer_id[:8]}_{new_alias}"
            try:
                with manager._global_schema_lock():
                    global_data = manager._read_global_yaml()
                    copied = 0
                    for table_name in selected:
                        src_key = f"{owner_schema_alias}.{table_name}"
                        if src_key in global_data:
                            global_data[f"{new_schema_alias}.{table_name}"] = global_data[src_key]
                            copied += 1
                    if copied:
                        manager._write_global_yaml(global_data)
                        logger.info(f"share fork: reused cached schema for {copied}/{len(selected)} table(s) on '{row['alias']}'")
                # Re-load so schema_info picks up what we just copied — the
                # manager's __init__ already ran _load_schema() once, before
                # this copy happened, so it needs refreshing.
                manager.schema_info = manager._load_schema()
            except Exception as e:
                logger.warning(f"share fork: failed to copy cached schema for '{row['alias']}': {e}")

            manager.selected_tables = selected
            _get_nb_sources(viewer_id, notebook["id"])[new_alias] = manager
            _get_nb_engine(viewer_id, notebook["id"]).add_source(new_alias, manager)
            user_store.save_data_source(
                viewer_id, new_alias, row["db_type"],
                row["connection_config"], selected, notebook_id=notebook["id"],
            )
            if selected:
                # Only the tables that weren't in the owner's cache (if any)
                # will actually hit the LLM here — set_selected_tables()
                # skips ones that already have descriptions.
                asyncio.ensure_future(_run_schema_generation(
                    manager, selected, openai_api_key or "", viewer_id, notebook_id=notebook["id"]
                ))
        if src_rows:
            bump_and_push(viewer_id)
    except Exception as e:
        logger.error(f"share fork: failed to fork data sources: {e}", exc_info=True)

    # 4. Fork documents — copy the underlying file and re-embed into the
    #    viewer's own per-notebook collection (documents aren't user-scoped
    #    on disk, only notebook-scoped, but embeddings live in a collection
    #    keyed by notebook_id so they still need re-ingesting here).
    try:
        docs = document_manager.list_documents(notebook_id=src_nb_id) or []
        for summary in docs:
            full_meta = document_manager.metadata.get(summary["doc_id"], {})
            stored_name = full_meta.get("stored_filename")
            if not stored_name:
                continue
            src_path = document_manager.storage_path / stored_name
            if not src_path.exists():
                continue
            with open(src_path, "rb") as f:
                raw = f.read()
            result = await document_manager.upload_document(
                filename=summary["filename"],
                content=raw,
                category=summary.get("category", "general"),
                description=summary.get("description", ""),
                metadata_tags=summary.get("tags") or {},
                notebook_id=notebook["id"],
            )
            nb_rag_engine = get_rag_engine(notebook["id"])
            nb_rag_engine.ingest_document(
                doc_id=result["doc_id"],
                text=result["text_content"],
                metadata={
                    "filename": summary["filename"],
                    "category": summary.get("category", "general"),
                    "description": summary.get("description", ""),
                },
            )
    except Exception as e:
        logger.error(f"share fork: failed to fork documents: {e}", exc_info=True)

    # 5. Fork Power BI dashboards — copy the config row under the viewer's
    #    own user_id (dashboards are strictly owner-scoped), then link it to
    #    the new notebook.
    try:
        dash_rows = database.get_dashboards_for_notebook(src_nb_id, owner_id)
        for d in dash_rows:
            new_dash = database.create_dashboard(viewer_id, d["name"], d["type"], d["config"])
            database.connect_dashboard_to_notebook(notebook["id"], new_dash["id"], viewer_id)
    except Exception as e:
        logger.error(f"share fork: failed to fork dashboards: {e}", exc_info=True)

    user_store.record_share_fork(token, viewer_id, notebook["id"], chat["id"])
    return {"notebook_id": notebook["id"], "chat_id": chat["id"]}


# ── Message persistence helper ─────────────────────────────────────────────────
#
# We save ONLY the assistant row. The question field on that row is enough
# to reconstruct the user bubble on the frontend — this prevents duplicates
# when chat history is loaded back (one DB row = one user+assistant pair).
#
def _persist_messages(chat_id: Optional[str], user_id: Optional[str],
                      question: str, source_filter: str, response: "CompleteResponse"):
    if not chat_id or not user_id:
        return
    import uuid
    try:
        asst_msg_id = str(uuid.uuid4())
        user_store.save_message(
            chat_id=chat_id,
            msg_id=asst_msg_id,
            role="assistant",
            question=question,           # stored so we can rebuild the user bubble
            sql_query=response.sql_query,
            summary=response.summary,
            chart_data=response.chart_data,
            chart_type=response.chart_type,
            columns=response.columns,
            data=response.data,
            error=response.error,
            follow_up_questions=response.follow_up_questions,
            source_filter=source_filter,
            referenced_documents=response.referenced_documents,
        )
    except Exception as e:
        logger.warning(f"Failed to persist message for chat {chat_id}: {e}")


# ── Main endpoint ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    manager = get_active_db_manager()
    db_type_val = None
    if manager is not None:
        sources = getattr(manager, "list_sources", None)
        if sources:
            db_type_val = "federated" if len(sources()) > 1 else next(iter(sources().values()), None)
        else:
            db_type_val = getattr(manager, "db_type", None)
    return {
        "status": "ok",
        "service": "sql-agent",
        "db_connected": manager is not None,
        "db_type": db_type_val,
    }


@app.post("/api/process-question", response_model=CompleteResponse)
async def process_question(request: QueryRequest, http_request: Request):
    source_filter = (request.source_filter or "all").lower()
    user          = auth_module.get_optional_user(http_request)
    user_id       = user["user_id"] if user else None
    persona       = user.get("persona") if user else None
    nb_id         = request.notebook_id
    chat_id       = request.chat_id
    original_question = request.question

    # Use notebook-scoped db manager
    db_manager = get_active_db_manager(user_id, nb_id)

    # Priority: Chat persona > User persona
    if chat_id:
        try:
            chat_persona = user_store.get_chat_persona(chat_id)
            if chat_persona:
                persona = chat_persona
        except Exception:
            pass

    # Inject project instructions
    if nb_id and user_id:
        try:
            nb = user_store.get_notebook(nb_id, user_id)
            if nb and nb.get("instructions"):
                request = QueryRequest(
                    question=request.question + f"\n\n[Project instructions: {nb['instructions']}]",
                    previous_question=request.previous_question,
                    previous_sql=request.previous_sql,
                    previous_summary=request.previous_summary,
                    source_filter=request.source_filter,
                    excluded_source_ids=request.excluded_source_ids,
                    notebook_id=nb_id,
                    chat_id=chat_id,
                )
        except Exception:
            pass

    # Per-source exclusion — the ONLY filtering mechanism now (there is no
    # category-level toggle in the UI anymore). Dashboard and document
    # exclusion are simple list/result filters (handled at their respective
    # call sites below and in _process_document_only / _run_rag_sync /
    # _process_routed). Database exclusion is different: db_manager is a
    # single federated engine merging every connected DB source, with no
    # existing per-request scoping — so rather than modifying that engine,
    # we use its own public add_source/remove_source/get_source API to
    # temporarily pull excluded aliases out for the duration of THIS
    # request only, restoring them in `finally` no matter what happens
    # (including exceptions), so no other concurrent request or later
    # question ever sees them missing.
    excluded_ids = set(request.excluded_source_ids or [])
    removed_db_sources: Dict[str, Any] = {}
    if db_manager and excluded_ids and hasattr(db_manager, "list_sources"):
        try:
            for alias in list(db_manager.list_sources().keys()):
                if alias in excluded_ids:
                    mgr = db_manager.get_source(alias)
                    if mgr is not None:
                        removed_db_sources[alias] = mgr
                        db_manager.remove_source(alias)
        except Exception as e:
            logger.warning(f"Failed to apply per-source database exclusion: {e}")

    # ── Category participation is derived ENTIRELY from individual source
    # enablement now — there is no separate "database/documents/dashboard"
    # toggle in the UI. A category only participates in this question if at
    # least one of its own connected sources is still enabled (i.e. wasn't
    # unchecked in the Sources panel) after exclusion is applied above.
    has_database = bool(
        db_manager and hasattr(db_manager, "list_sources") and len(db_manager.list_sources()) > 0
    )

    has_documents = False
    try:
        docs = document_manager.list_documents(notebook_id=nb_id) if nb_id else document_manager.list_documents()
        has_documents = any(str(d.get("doc_id")) not in excluded_ids for d in docs)
    except Exception as e:
        logger.warning(f"Failed to determine document participation: {e}")

    has_dashboard = False
    try:
        from database import get_dashboards_for_notebook, get_dashboards_for_user
        dashboards_avail = (
            get_dashboards_for_notebook(nb_id, user_id) if nb_id and user_id
            else (get_dashboards_for_user(user_id) if user_id else [])
        )
        has_dashboard = any(str(d.get("id")) not in excluded_ids for d in dashboards_avail)
    except Exception as e:
        logger.warning(f"Failed to determine dashboard participation: {e}")

    logger.info(f"process_question has_database={has_database} has_documents={has_documents} "
                f"has_dashboard={has_dashboard} excluded={excluded_ids or None} user={user_id} persona={persona}")

    response: CompleteResponse

    try:
        # ── Nothing enabled — every connected source was individually
        # unchecked. Report this cleanly rather than falling through to
        # _process_routed with nothing to actually query.
        if not has_database and not has_documents and not has_dashboard:
            response = CompleteResponse(
                success=False, sql_query=None, data=None, columns=None,
                summary="No data sources are currently enabled. Check at least one source "
                        "in the Sources panel to ask a question.",
                follow_up_questions=None, chart_data=None, chart_type="none",
                error=None, answered_by=None,
            )
            _persist_messages(chat_id, user_id, original_question, source_filter, response)
            return response

        # ── Dashboard only ─────────────────────────────────────────────
        if has_dashboard and not has_database and not has_documents:
            from database import get_dashboards_for_user
            dashboards = [
                d for d in get_dashboards_for_user(user_id)
                if str(d.get('id')) not in excluded_ids
            ]
            if not dashboards:
                raise HTTPException(status_code=400, detail="No dashboards connected")
            dashboard = next((d for d in dashboards if d['type'] == 'powerbi'), None)
            if not dashboard:
                raise HTTPException(status_code=400, detail="No Power BI dashboard connected")
            import powerbi_service
            try:
                result = powerbi_service.run_chat(
                    creds=dashboard['config'],
                    dashboard_id=dashboard['id'],
                    message=request.question,
                )
                response = CompleteResponse(
                    success=True,
                    sql_query=result.get('dax_query'),
                    data=result.get('rows'),
                    columns=None,
                    summary=result.get('insight'),
                    follow_up_questions=None,
                    chart_data=None,
                    chart_type="none",
                    error=None,
                    answered_by="dashboard",
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Dashboard query failed: {str(e)}")
            _persist_messages(chat_id, user_id, original_question, source_filter, response)
            return response

        # ── Database only ──────────────────────────────────────────────
        if has_database and not has_documents and not has_dashboard:
            if not db_manager:
                raise HTTPException(status_code=400, detail="No data source connected")
            response = await _process_database_only(request, db_manager, persona)
            _persist_messages(chat_id, user_id, original_question, source_filter, response)
            return response

        # ── Documents only ─────────────────────────────────────────────
        if has_documents and not has_database and not has_dashboard:
            response = _process_document_only(request, persona)
            _persist_messages(chat_id, user_id, original_question, source_filter, response)
            return response

        # ── All sources — hit everything, synthesise at the end ───────────────
        response = await _process_routed(request, db_manager, user_id, persona)
        _persist_messages(chat_id, user_id, original_question, source_filter, response)
        return response
    finally:
        for alias, mgr in removed_db_sources.items():
            try:
                db_manager.add_source(alias, mgr)
            except Exception as e:
                logger.error(f"Failed to restore excluded source '{alias}' after request: {e}")


@app.post("/api/generate-sql", response_model=SQLResponse)
async def generate_sql_only(request: QueryRequest):
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    sql_generator = SQLGenerator(api_key=openai_api_key, db_manager=db_manager)
    result = sql_generator.generate_sql(
        question=request.question,
        previous_question=request.previous_question,
        previous_sql=request.previous_sql,
    )
    return SQLResponse(**{k: result.get(k) for k in ["success", "sql_query", "error"]})


@app.post("/api/execute-sql")
async def execute_sql(body: Dict[str, Any]):
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    sql = body.get("sql")
    if not sql:
        raise HTTPException(status_code=400, detail="sql field is required")
    try:
        df = db_manager.execute_query(sql)
        return {
            "success":   True,
            "data":      json.loads(df.to_json(orient="records", date_format="iso")),
            "columns":   df.columns.tolist(),
            "row_count": len(df),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/refresh-schema")
async def refresh_schema():
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    db_manager.reload_schema()
    return {"success": True, "tables": db_manager.get_available_tables()}


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENT MANAGEMENT & RAG ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form("general"),
    description: str = Form(""),
    notebook_id: Optional[str] = Form(None),
):
    try:
        filename = file.filename
        file_ext = filename.split(".")[-1].lower() if "." in filename else ""

        if file_ext not in ["pdf", "docx", "doc", "pptx", "txt", "csv"]:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format: {file_ext}. Supported: PDF, DOCX, PPTX, CSV, TXT",
            )

        content = await file.read()

        result = await document_manager.upload_document(
            filename=filename,
            content=content,
            category=category,
            description=description,
            notebook_id=notebook_id,
        )

        doc_id       = result["doc_id"]
        text_content = result["text_content"]

        # Use notebook-specific RAGEngine for per-project collection isolation
        nb_rag_engine = get_rag_engine(notebook_id)
        chunk_count = nb_rag_engine.ingest_document(
            doc_id=doc_id,
            text=text_content,
            metadata={"filename": filename, "category": category, "description": description},
        )

        logger.info(f"Document uploaded and ingested: {doc_id} ({chunk_count} chunks) in notebook: {notebook_id or 'default'}")

        return {
            "success":     True,
            "doc_id":      doc_id,
            "filename":    filename,
            "category":    category,
            "chunk_count": chunk_count,
            "text_length": len(text_content),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Document upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


class PasteTextRequest(BaseModel):
    title: str = "Pasted Text"
    text: str
    category: str = "general"
    description: str = ""
    notebook_id: Optional[str] = None


@app.post("/api/documents/upload-text")
async def upload_pasted_text(body: PasteTextRequest):
    """Accept raw pasted text, store it as a .txt document and ingest into RAG."""
    try:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="Text content cannot be empty")
        filename = f"{body.title.strip() or 'Pasted Text'}.txt"
        content  = body.text.encode("utf-8")
        result = await document_manager.upload_document(
            filename=filename, content=content,
            category=body.category, description=body.description,
            notebook_id=body.notebook_id,
        )
        doc_id       = result["doc_id"]
        text_content = result["text_content"]
        nb_rag_engine = get_rag_engine(body.notebook_id)
        chunk_count = nb_rag_engine.ingest_document(
            doc_id=doc_id, text=text_content,
            metadata={"filename": filename, "category": body.category,
                      "description": body.description, "source_type": "pasted_text"},
        )
        logger.info(f"Pasted text ingested: {doc_id} ({chunk_count} chunks)")
        return {"success": True, "doc_id": doc_id, "filename": filename,
                "category": body.category, "chunk_count": chunk_count, "text_length": len(text_content)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pasted text upload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# ── NOTE: scrape-url MUST be defined before any /api/documents/{doc_id} routes.
# FastAPI matches routes top-to-bottom; if a {doc_id} wildcard route appears first
# it captures "scrape-url" as a doc_id and returns 405 Method Not Allowed.

class ScrapeUrlRequest(BaseModel):
    url: str
    notebook_id: Optional[str] = None


@app.post("/api/documents/scrape-url")
async def scrape_url(body: ScrapeUrlRequest, request: Request):
    """
    Scrape a public URL, extract clean structured text, and ingest into RAG.
    Single page — simple and reliable.
    """
    import re as _re
    from urllib.parse import urlparse

    auth_module.get_current_user(request)

    # ── Validate URL ──────────────────────────────────────────────────────────
    raw_url = body.url.strip()
    if not raw_url:
        raise HTTPException(status_code=400, detail="URL is required")
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url
    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL — could not parse domain")

    import ipaddress
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise HTTPException(status_code=400, detail="Private/internal URLs are not allowed")
    except ValueError:
        pass  # it's a domain name, not an IP

    domain = parsed.netloc

    # ── Import deps ───────────────────────────────────────────────────────────
    try:
        import httpx
        from bs4 import BeautifulSoup, Tag
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Scraping dependencies missing. Run: pip install httpx beautifulsoup4 lxml"
        )

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Upgrade-Insecure-Requests": "1",
    }

    # ══════════════════════════════════════════════════════════════════════════
    # LAYERED FETCH STRATEGY
    # Layer 1: Playwright — full JS rendering, handles SPAs (React/Vue/Angular)
    # Layer 2: Jina Reader  — cloud headless browser, bypasses many bot shields
    # Layer 3: httpx        — fast, works for static/server-rendered sites
    # Layer 4: Error        — clear message to use Paste text instead
    # We pick whichever layer returns the most content (>= MIN_CONTENT_LEN chars)
    # ══════════════════════════════════════════════════════════════════════════
    MIN_CONTENT_LEN = 500   # minimum chars of extracted text to consider a fetch successful
    html            = None
    extracted_text  = None  # Jina returns pre-cleaned text, skips BeautifulSoup entirely
    page_title      = domain
    fetch_method    = "unknown"

    # ── Layer 1: Playwright ───────────────────────────────────────────────────
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            ctx  = await browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
                java_script_enabled=True,
            )
            page = await ctx.new_page()
            # Hide webdriver flag — helps bypass basic bot detection
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            await page.goto(raw_url, wait_until="networkidle", timeout=30000)
            # Scroll to trigger lazy-loaded content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            html = await page.content()
            await browser.close()
        logger.info(f"[scrape] Layer 1 (Playwright) fetched {raw_url} — {len(html)} chars of HTML")
        fetch_method = "playwright"
    except ImportError:
        logger.info("[scrape] Layer 1 skipped — Playwright not installed")
    except Exception as pw_exc:
        logger.warning(f"[scrape] Layer 1 (Playwright) failed: {pw_exc!s:.200}")

    # Check if Playwright gave us enough content after extraction
    if html:
        try:
            _soup = BeautifulSoup(html, "lxml")
            _visible = _soup.get_text(separator=" ", strip=True)
            if len(_visible) < MIN_CONTENT_LEN:
                logger.info(f"[scrape] Layer 1 content too thin ({len(_visible)} chars) — trying next layer")
                html = None
        except Exception:
            html = None

    # ── Layer 2: Jina Reader ──────────────────────────────────────────────────
    # Jina runs its own headless browser farm and returns clean markdown text.
    # Returns pre-extracted text so we skip BeautifulSoup entirely for this layer.
    if not html:
        try:
            jina_url = f"https://r.jina.ai/{raw_url}"
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                jina_resp = await client.get(
                    jina_url,
                    headers={
                        "Accept": "text/plain",
                        "X-Return-Format": "text",
                        "User-Agent": HEADERS["User-Agent"],
                    }
                )
            if jina_resp.status_code == 200 and len(jina_resp.text.strip()) >= MIN_CONTENT_LEN:
                extracted_text = jina_resp.text.strip()
                # Extract title from first line of Jina markdown (format: "Title: ...")
                for line in extracted_text.splitlines()[:5]:
                    if line.lower().startswith("title:"):
                        page_title = line.split(":", 1)[1].strip()
                        break
                fetch_method = "jina"
                logger.info(f"[scrape] Layer 2 (Jina) fetched {raw_url} — {len(extracted_text)} chars")
            else:
                logger.info(f"[scrape] Layer 2 (Jina) returned {jina_resp.status_code} or thin content — trying next layer")
        except Exception as jina_exc:
            logger.warning(f"[scrape] Layer 2 (Jina) failed: {jina_exc!s:.200}")

    # ── Layer 3: httpx ────────────────────────────────────────────────────────
    if not html and not extracted_text:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                resp = await client.get(raw_url, headers=HEADERS)
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                html = resp.text
                fetch_method = "httpx"
                logger.info(f"[scrape] Layer 3 (httpx) fetched {raw_url} — {len(html)} chars of HTML")
            elif resp.status_code == 403:
                logger.warning(f"[scrape] Layer 3 (httpx) got 403 for {raw_url}")
            else:
                logger.warning(f"[scrape] Layer 3 (httpx) got {resp.status_code} for {raw_url}")
        except Exception as http_exc:
            logger.warning(f"[scrape] Layer 3 (httpx) failed: {http_exc!s:.200}")

    # ── Layer 4: fail with clear message ─────────────────────────────────────
    if not html and not extracted_text:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not extract content from this URL. The site may require login, "
                "use heavy bot protection, or be JavaScript-only. "
                "Try using 'Paste text' to add the content manually."
            )
        )

    # ── Extract structured text (only needed for Playwright/httpx html) ─────
    # Jina already returns clean text — skip BeautifulSoup for that path
    if html and not extracted_text:
        def _extract(html_str):
            soup = BeautifulSoup(html_str, "lxml")

            title_tag = soup.find("title")
            _title = title_tag.get_text(strip=True) if title_tag else domain

            # ── Extract JSON-LD structured data ──
            jsonld_texts = []
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    import json as _json
                    data = _json.loads(script.string or "")
                    def _flatten_jsonld(obj, depth=0):
                        if depth > 3 or not isinstance(obj, dict):
                            return []
                        parts = []
                        for key in ("name", "headline", "description", "text",
                                    "articleBody", "abstract", "keywords",
                                    "url", "datePublished", "author"):
                            val = obj.get(key)
                            if isinstance(val, str) and val.strip():
                                parts.append(f"{key}: {val.strip()}")
                            elif isinstance(val, list):
                                for v in val:
                                    if isinstance(v, str) and v.strip():
                                        parts.append(f"{key}: {v.strip()}")
                                    elif isinstance(v, dict):
                                        parts.extend(_flatten_jsonld(v, depth + 1))
                        for val in obj.values():
                            if isinstance(val, dict):
                                parts.extend(_flatten_jsonld(val, depth + 1))
                        return parts
                    jsonld_texts.extend(_flatten_jsonld(data if isinstance(data, dict) else {}))
                except Exception:
                    pass

            # ── Extract meta tags ──
            meta_texts = []
            for meta in soup.find_all("meta"):
                name    = (meta.get("name") or meta.get("property") or "").lower()
                content = (meta.get("content") or "").strip()
                if not content:
                    continue
                if name in ("description", "og:description", "twitter:description",
                            "og:title", "twitter:title", "keywords",
                            "citation_title", "citation_abstract",
                            "dc.description", "dc.title", "dc.subject"):
                    meta_texts.append(f"{name}: {content}")

            # ── Remove boilerplate ──
            for tag in soup(["script", "style", "noscript", "nav", "footer",
                              "header", "aside", "form", "iframe", "svg",
                              "button", "input", "select", "textarea", "meta", "link"]):
                tag.decompose()

            # ── Find main content area ──
            main = (
                soup.find("main") or
                soup.find("article") or
                soup.find(attrs={"role": "main"}) or
                soup.find(id=_re.compile(r"content|main|article|body", _re.I)) or
                soup.find(class_=_re.compile(r"content|main|article|post|entry|page-body", _re.I)) or
                soup.body or soup
            )

            sections = []
            current_heading = None
            current_paras = []

            def flush():
                if current_paras:
                    block = []
                    if current_heading:
                        block.append("## " + current_heading)
                    block.extend(current_paras)
                    sections.append("\n".join(block))

            HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
            CONTENT_TAGS = {"p", "li", "td", "th", "blockquote", "pre", "code",
                            "dd", "dt", "figcaption", "caption", "div", "section",
                            "article", "span"}

            for elem in main.descendants:
                if not isinstance(elem, Tag):
                    continue
                tag_name = elem.name.lower() if elem.name else ""
                if tag_name in HEADING_TAGS:
                    txt = elem.get_text(separator=" ", strip=True)
                    if txt and len(txt) > 2:
                        flush()
                        current_heading = txt
                        current_paras = []
                elif tag_name in CONTENT_TAGS:
                    parent = elem.parent
                    if parent and isinstance(parent, Tag) and parent.name in CONTENT_TAGS:
                        continue
                    txt = elem.get_text(separator=" ", strip=True)
                    if txt and len(txt) > 15 and txt != current_heading:
                        txt = _re.sub(r"\s+", " ", txt).strip()
                        if not current_paras or current_paras[-1] != txt:
                            current_paras.append(txt)
            flush()

            # ── Assemble ──
            parts = []
            if meta_texts:
                parts.append("## Page Metadata\n" + "\n".join(meta_texts))
            if jsonld_texts:
                parts.append("## Structured Data (JSON-LD)\n" + "\n".join(jsonld_texts))
            if sections:
                parts.extend(sections)

            dom_text_len = sum(len(s) for s in sections)
            if not parts or (dom_text_len < 300 and not meta_texts and not jsonld_texts):
                raw = soup.get_text(separator="\n", strip=True)
                raw = _re.sub(r"\n{3,}", "\n\n", raw)
                parts = [raw[:30000]]
            elif dom_text_len < 300 and (meta_texts or jsonld_texts):
                logger.info(f"[scrape] SPA fallback — using meta/JSON-LD ({len(parts)} blocks)")

            full = "URL: " + raw_url + "\nTitle: " + _title + "\nDomain: " + domain + "\n\n" + "\n\n".join(parts)
            if len(full) > 120000:
                full = full[:120000] + "\n\n[Content truncated]"
            return _title, full

        try:
            page_title, extracted_text = _extract(html)
        except Exception as exc:
            logger.error(f"[scrape] extraction error: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Text extraction failed: {exc}")
    elif extracted_text:
        # Jina already gave us clean text — just prepend URL/domain header if missing
        if not extracted_text.startswith("URL:"):
            extracted_text = f"URL: {raw_url}\nTitle: {page_title}\nDomain: {domain}\n\n{extracted_text}"
        if len(extracted_text) > 120000:
            extracted_text = extracted_text[:120000] + "\n\n[Content truncated]"

    if not extracted_text or len(extracted_text.strip()) < 50:
        raise HTTPException(
            status_code=422,
            detail="Very little readable text found. The page may require login or JavaScript. Try 'Paste text' instead."
        )

    logger.info(f"[scrape] method={fetch_method} chars={len(extracted_text)} url={raw_url}")

    # ── Ingest into RAG ───────────────────────────────────────────────────────
    safe_title = _re.sub(r"[^\w\s\-.]", "", page_title).strip()
    safe_title = _re.sub(r"\s+", "_", safe_title)[:80] or _re.sub(r"[^\w\-.]", "_", domain)
    filename = safe_title + ".website.txt"

    try:
        result = await document_manager.upload_document(
            filename=filename,
            content=extracted_text.encode("utf-8"),
            category="general",
            description="Scraped from " + domain,
            metadata_tags={
                "source_type": "website",
                "url":         raw_url,
                "domain":      domain,
                "page_title":  page_title,
                "fetch_method": fetch_method,
            },
            notebook_id=body.notebook_id,
        )
        doc_id       = result["doc_id"]
        text_content = result["text_content"]

        nb_rag_engine = get_rag_engine(body.notebook_id)
        chunk_count = nb_rag_engine.ingest_document(
            doc_id=doc_id,
            text=text_content,
            metadata={
                "filename":    filename,
                "source_type": "website",
                "url":         raw_url,
                "domain":      domain,
            },
        )

        logger.info(f"[scrape] {raw_url} -> {doc_id} ({chunk_count} chunks) method={fetch_method} notebook={body.notebook_id or 'default'}")
        return {
            "success":      True,
            "doc_id":       doc_id,
            "url":          raw_url,
            "domain":       domain,
            "page_title":   page_title,
            "chunk_count":  chunk_count,
            "text_length":  len(extracted_text),
            "fetch_method": fetch_method,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[scrape] ingest failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ingest failed: {exc}")


@app.get("/api/documents/{doc_id}/text")
async def get_document_text(doc_id: str):
    """Return raw text content of a document (used by the pasted-text editor)."""
    try:
        text = document_manager.get_document_text(doc_id)
        if text is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"doc_id": doc_id, "text": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UpdateTextRequest(BaseModel):
    text: str
    description: str = ""
    notebook_id: Optional[str] = None


@app.put("/api/documents/{doc_id}/update-text")
async def update_pasted_text(doc_id: str, body: UpdateTextRequest):
    """Re-index updated pasted text — removes old chunks and ingests new ones."""
    try:
        if not body.text.strip():
            raise HTTPException(status_code=400, detail="Text content cannot be empty")
        nb_rag_engine = get_rag_engine(body.notebook_id)
        nb_rag_engine.remove_document(doc_id)
        chunk_count = nb_rag_engine.ingest_document(
            doc_id=doc_id, text=body.text,
            metadata={"description": body.description, "source_type": "pasted_text"},
        )
        logger.info(f"Pasted text updated: {doc_id} ({chunk_count} chunks)")
        return {"success": True, "doc_id": doc_id, "chunk_count": chunk_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pasted text update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents(category: Optional[str] = None, notebook_id: Optional[str] = None):
    try:
        documents = document_manager.list_documents(category=category, notebook_id=notebook_id)
        try:
            nb_rag_engine = get_rag_engine(notebook_id)
            stats = nb_rag_engine.get_collection_stats()
        except Exception as stats_err:
            logger.warning(f"Could not fetch RAG stats: {stats_err}")
            stats = {}
        return {
            "success":     True,
            "documents":   documents,
            "total_count": len(documents),
            "rag_stats":   stats,
        }
    except Exception as e:
        logger.error(f"Failed to list documents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    try:
        # Get document metadata to find notebook_id
        doc_meta = document_manager.get_document_metadata(doc_id)
        notebook_id = doc_meta.get("notebook_id") if doc_meta else None

        # Use notebook-specific RAGEngine for deletion from correct collection
        nb_rag_engine = get_rag_engine(notebook_id)
        chunk_count = nb_rag_engine.remove_document(doc_id)
        success     = document_manager.delete_document(doc_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

        logger.info(f"Document deleted: {doc_id} ({chunk_count} chunks removed)")
        return {"success": True, "doc_id": doc_id, "chunks_removed": chunk_count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete document {doc_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}")
async def get_document_info(doc_id: str):
    try:
        metadata = document_manager.get_document_metadata(doc_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
        return {"success": True, "metadata": metadata}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}/download")
async def download_document(doc_id: str):
    try:
        metadata = document_manager.get_document_metadata(doc_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

        file_path = document_manager.storage_path / metadata["stored_filename"]
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"Document file not found")

        media_types = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc": "application/msword",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "ppt": "application/vnd.ms-powerpoint",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xls": "application/vnd.ms-excel",
            "txt": "text/plain",
            "csv": "text/csv",
        }
        media_type = media_types.get(metadata["file_extension"], "application/octet-stream")

        from fastapi.responses import FileResponse
        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=metadata["filename"]
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}/content")
async def get_document_content(doc_id: str):
    try:
        metadata = document_manager.get_document_metadata(doc_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

        text_content = document_manager.get_document_text(doc_id)
        if text_content is None:
            raise HTTPException(status_code=500, detail=f"Failed to retrieve document content")

        return {
            "success": True,
            "filename": metadata["filename"],
            "extension": metadata["file_extension"],
            "content": text_content,
            "text_length": len(text_content),
            "metadata": {
                "category": metadata["category"],
                "description": metadata["description"],
                "upload_timestamp": metadata["upload_timestamp"],
                "file_size": metadata["file_size"],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents/{doc_id}/preview-pdf")
async def preview_document_as_pdf(doc_id: str):
    """
    On-demand PPTX/PPT → PDF conversion for preview purposes, reusing the
    frontend's existing pdf.js renderer (DocumentViewer.tsx already renders
    real PDFs page-by-page — this just gives it a PDF to point at for
    PowerPoint files too, no new client-side rendering code needed).

    Converts via LibreOffice (see ppttopdf.py) and caches the result under
    storage_path/converted/{doc_id}.pdf — conversion takes a few seconds
    (LibreOffice startup dominates), so repeat preview opens for the same
    document should hit the cache, not reconvert every time.
    """
    try:
        metadata = document_manager.get_document_metadata(doc_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

        ext = metadata["file_extension"].lower()
        if ext not in ("pptx", "ppt"):
            raise HTTPException(
                status_code=400,
                detail=f"preview-pdf only supports .pptx/.ppt files, got .{ext}",
            )

        source_path = document_manager.storage_path / metadata["stored_filename"]
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="Document file not found")

        from ppttopdf import convert_ppt_to_pdf_cached
        cache_dir = document_manager.storage_path / "converted"

        try:
            # Blocking subprocess call — run off the event loop so one slow
            # LibreOffice conversion doesn't stall every other request.
            pdf_path = await asyncio.to_thread(
                convert_ppt_to_pdf_cached,
                str(source_path), str(cache_dir), doc_id,
            )
        except FileNotFoundError as e:
            # LibreOffice itself isn't installed/found on this machine —
            # a deployment issue, not a bad-file issue, so 503 not 500.
            raise HTTPException(status_code=503, detail=f"PDF conversion unavailable: {e}")
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=f"PDF conversion failed: {e}")

        from fastapi.responses import FileResponse
        return FileResponse(
            path=pdf_path,
            media_type="application/pdf",
            filename=Path(metadata["filename"]).stem + ".pdf",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/documents/business-rules")
async def upload_business_rules(
    content: str = Form(...),
    title: str = Form("Business Rules"),
    description: str = Form(""),
):
    try:
        if not content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")

        filename      = f"{title.replace(' ', '_')}.txt"
        content_bytes = content.encode("utf-8")

        result = await document_manager.upload_document(
            filename=filename,
            content=content_bytes,
            category="business_rules",
            description=description,
        )

        doc_id       = result["doc_id"]
        text_content = result["text_content"]

        chunk_count = rag_engine.ingest_document(
            doc_id=doc_id,
            text=text_content,
            metadata={"filename": filename, "category": "business_rules", "description": description},
        )

        logger.info(f"Business rules uploaded: {doc_id} ({chunk_count} chunks)")
        return {"success": True, "doc_id": doc_id, "chunk_count": chunk_count}

    except Exception as e:
        logger.error(f"Business rules upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA ENHANCEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class ColumnEnhancement(BaseModel):
    table_name: str
    column_name: str
    business_description: Optional[str] = None
    example_values: Optional[List[str]] = None
    business_rules: Optional[List[str]] = None
    common_queries: Optional[List[str]] = None


class BulkEnhancement(BaseModel):
    enhancements: Dict[str, Dict[str, Dict[str, Any]]]


@app.post("/api/schema/enhance-column")
async def enhance_column(enhancement: ColumnEnhancement):
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    try:
        success = db_manager.enhance_column_metadata(
            table_name=enhancement.table_name,
            column_name=enhancement.column_name,
            business_description=enhancement.business_description,
            example_values=enhancement.example_values,
            business_rules=enhancement.business_rules,
            common_queries=enhancement.common_queries,
        )
        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Table or column not found: {enhancement.table_name}.{enhancement.column_name}",
            )
        return {"success": True, "message": "Column metadata enhanced successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to enhance column: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/schema/enhance-bulk")
async def enhance_bulk(enhancement: BulkEnhancement):
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    try:
        results = db_manager.bulk_enhance_from_dict(enhancement.enhancements)
        return {
            "success": True,
            "results": results,
            "message": (
                f"Enhanced {results['success']} columns, "
                f"{results['failed']} failed, {results['skipped']} skipped"
            ),
        }
    except Exception as e:
        logger.error(f"Bulk enhancement failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schema/enhanced")
async def get_enhanced_schema():
    db_manager = get_active_db_manager()
    if not db_manager:
        raise HTTPException(status_code=400, detail="No data source connected")
    try:
        context = db_manager.get_enhanced_schema_context()
        return {"success": True, "enhanced_schema": context}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Notebook Endpoints ─────────────────────────────────────────────────────────

class NotebookCreate(BaseModel):
    name: str = "Untitled project"
    persona: Optional[str] = None
    instructions: Optional[str] = None
    description: Optional[str] = None

class NotebookUpdate(BaseModel):
    name: Optional[str] = None
    persona: Optional[str] = None
    instructions: Optional[str] = None
    description: Optional[str] = None


@app.get("/api/notebooks")
async def list_notebooks(request: Request):
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_store.list_notebooks(user["user_id"])


@app.post("/api/notebooks")
async def create_notebook(body: NotebookCreate, request: Request):
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    notebook = user_store.create_notebook(
        user["user_id"],
        name=body.name,
        persona=body.persona,
        instructions=body.instructions,
        description=body.description,
    )
    return notebook


@app.get("/api/notebooks/{notebook_id}")
async def get_notebook(notebook_id: str, request: Request):
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    notebook = user_store.get_notebook(notebook_id, user["user_id"])
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return notebook


@app.patch("/api/notebooks/{notebook_id}")
async def update_notebook(notebook_id: str, body: NotebookUpdate, request: Request):
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    notebook = user_store.update_notebook_config(
        notebook_id, user["user_id"],
        name=body.name.strip() if body.name else None,
        persona=body.persona,
        instructions=body.instructions,
        description=body.description,
    )
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return notebook


@app.delete("/api/notebooks/{notebook_id}")
async def delete_notebook(notebook_id: str, request: Request):
    """Delete a notebook."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    notebook = user_store.get_notebook(notebook_id, user["user_id"])
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    user_store.delete_notebook(notebook_id, user["user_id"])
    return {"deleted": True}


@app.get("/api/notebooks/{notebook_id}/sources")
async def get_notebook_sources(notebook_id: str, request: Request):
    """Get all data sources for a notebook."""
    user = auth_module.get_optional_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sources = user_store.get_notebook_data_sources(notebook_id, user["user_id"])
    return sources


# ── Dashboard Endpoints ────────────────────────────────────────────────────────

@app.get("/api/dashboards")
async def list_dashboards(request: Request):
    """List all dashboards for the current user."""
    user = auth_module.get_current_user(request)
    from database import get_dashboards_for_user
    dashboards = get_dashboards_for_user(user["user_id"])
    return dashboards


@app.get("/api/notebooks/{notebook_id}/dashboards")
async def get_notebook_dashboards(notebook_id: str, request: Request):
    """List dashboards connected to this specific notebook."""
    user = auth_module.get_current_user(request)
    from database import get_dashboards_for_notebook
    dashboards = get_dashboards_for_notebook(notebook_id, user["user_id"])
    return dashboards


@app.post("/api/dashboards/powerbi")
async def create_powerbi_dashboard(request: Request):
    """Create a new Power BI dashboard and connect it to a notebook.
    If a dashboard with the same report_id already exists for this user+notebook,
    returns the existing one instead of creating a duplicate.
    """
    user = auth_module.get_current_user(request)
    body = await request.json()

    from database import create_dashboard, connect_dashboard_to_notebook, get_dashboards_for_notebook

    config      = body.get("config", {})
    notebook_id = body.get("notebook_id")
    report_id   = config.get("report_id", "")

    # ── Dedup: if a dashboard with this report_id already exists in this notebook, return it ──
    if notebook_id and report_id:
        existing = get_dashboards_for_notebook(notebook_id, user["user_id"])
        for d in existing:
            if d.get("config", {}).get("report_id") == report_id:
                logger.info(f"Dashboard with report_id={report_id} already connected to notebook {notebook_id}, returning existing id={d['id']}")
                return d

    dashboard = create_dashboard(
        user_id=user["user_id"],
        name=body.get("name", "Untitled Dashboard"),
        type_="powerbi",
        config=config,
    )

    # Connect to notebook if notebook_id is provided
    if notebook_id:
        connect_dashboard_to_notebook(notebook_id, dashboard["id"], user["user_id"])

    # ── Kick off schema build in background only if no schema exists yet ──
    if report_id:
        import powerbi_service as _pbi_svc
        import threading
        existing_schema = _pbi_svc._load_yaml_schema(report_id)
        if not existing_schema:
            dashboard_id = dashboard["id"]
            def _build():
                try:
                    _pbi_svc.build_enriched_schema(config, dashboard_id)
                except Exception as e:
                    logger.error(f"Background schema build failed for dashboard {dashboard_id}: {e}")
            threading.Thread(target=_build, daemon=True).start()
            logger.info(f"Schema build started in background for dashboard {dashboard_id} (report_id={report_id})")
        else:
            logger.info(f"Schema already exists for report_id={report_id}, skipping build")

    return dashboard


@app.post("/api/dashboards/tableau")
async def create_tableau_dashboard(request: Request):
    """Create a new Tableau dashboard and connect it to a notebook."""
    user = auth_module.get_current_user(request)
    body = await request.json()

    from database import create_dashboard, connect_dashboard_to_notebook
    dashboard = create_dashboard(
        user_id=user["user_id"],
        name=body.get("name", "Untitled Dashboard"),
        type_="tableau",
        config=body.get("config", {}),
    )

    # Connect to notebook if notebook_id is provided
    notebook_id = body.get("notebook_id")
    if notebook_id:
        connect_dashboard_to_notebook(notebook_id, dashboard["id"], user["user_id"])

    return dashboard


@app.post("/api/notebooks/{notebook_id}/dashboards/{dashboard_id}/connect")
async def connect_existing_dashboard(notebook_id: str, dashboard_id: int, request: Request):
    """Connect an existing dashboard to a notebook."""
    user = auth_module.get_current_user(request)
    from database import get_dashboard_by_id, connect_dashboard_to_notebook

    dashboard = get_dashboard_by_id(dashboard_id, user["user_id"])
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    connect_dashboard_to_notebook(notebook_id, dashboard_id, user["user_id"])
    return {"success": True, "dashboard": dashboard}


@app.delete("/api/dashboards/{dashboard_id}")
async def delete_dashboard_endpoint(dashboard_id: int, request: Request):
    """Delete a dashboard."""
    user = auth_module.get_current_user(request)
    from database import delete_dashboard

    success = delete_dashboard(dashboard_id, user["user_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return {"deleted": True}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8005))
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True)