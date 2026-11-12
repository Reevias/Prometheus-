# Prometheus 3.0 — Global Open-Source Policy & Memory Chunking Audit

**Repository**: `Reevias/Prometheus-`  
**Branch**: `prometheus-v3`  
**Date**: November 12, 2026  
**Auditor**: Antigravity Engineering Architecture & Security Team  

---

## 1. Executive Summary

This comprehensive audit fulfills two strategic objectives for the Prometheus 3.0 development lifecycle:
1. **Permanent Open-Source Mandate**: Established a permanent, project-wide open-source policy in `AGENTS.md` and cross-referenced it across core architecture and roadmap documentation (`docs/architecture/PROMETHEUS_3_MASTER_PLAN.md`, `docs/implementation/ROADMAP.md`). Every original capability across milestones M0 through M10 must remain open source under the MIT License without undisclosed code or mandatory proprietary runtime dependencies.
2. **Memory Chunking Audit**: Discovered that **memory chunking is ALREADY implemented** in Prometheus 3.0 via `prometheus/memory/chunking.py` and `prometheus/memory/store.py`. It utilizes a sophisticated two-tier hierarchical Parent-Child chunking architecture with recursive splitting, structural heading/paragraph preservation, SQLite FTS5 BM25 indexing, SQLite vector embedding storage (`chunk_vectors`), and Reciprocal Rank Fusion (RRF) hybrid retrieval. 14 out of 14 unit tests in `tests/test_v14_chunking.py` and 22 tests in adjacent memory test suites are actively passing.

However, the memory system is classified as **C: Partially implemented, with important missing components**. While text chunking and hybrid search are functional, the system currently lacks multi-project isolation (no `project_id` column), AST/Tree-Sitter syntax-aware code block chunking, persistent heading hierarchy metadata, content-addressable stable chunk hashes, and automatic embedding model migration. These gaps represent the planned evolution for Milestone M7/M8 and should NOT be prematurely re-engineered during M2.

---

## 2. Open-Source Policy Changes

The project instructions in `AGENTS.md` were augmented with a prominent, permanent section titled `GLOBAL OPEN-SOURCE POLICY — MANDATORY FOR M0–M10`. 

Key policy tenets codified:
- **Public Availability**: All original Prometheus source code developed across M0–M10 must remain publicly accessible in the repository under the MIT License.
- **No Undisclosed Code**: Core functionality must not depend on hidden, binary-only, or proprietary backend code.
- **No Mandatory Closed Dependencies**: Introducing any mandatory proprietary runtime or build dependency requires explicit written approval from the project owner.
- **Strict Dependency Vetting (7 Steps)**: Identifies license, verifies usage rights, verifies MIT compatibility, checks commercial/redistribution restrictions, assesses security/maintenance health, documents purpose/version, and mandates open-source preference.
- **Licensing Disambiguation**: Codifies explicit distinctions between Open-Source Software (OSS), Open-Weight AI Models (which carry acceptable use riders and are NOT automatically OSS), Open Datasets, Free-Tier Proprietary APIs, and Paid Commercial Services.
- **Mandatory Enforcement**: Compliance is an immutable acceptance criterion for all milestones M0–M10.

---

## 3. Files Modified

| File Path | Nature of Change | Lines Changed |
| :--- | :--- | :--- |
| `AGENTS.md` | Added Section `## GLOBAL OPEN-SOURCE POLICY — MANDATORY FOR M0–M10` | +39 lines |
| `docs/architecture/PROMETHEUS_3_MASTER_PLAN.md` | Added Open-Source Integrity principle under Section 1 (Product Vision) | +1 line |
| `docs/implementation/ROADMAP.md` | Added mandatory open-source exit gate note across all milestones M0–M10 | +4 lines |

*Note: In accordance with project instructions, zero lines of application logic, existing tests, or working code were modified.*

---

## 4. Confirmation That Existing AGENTS.md Rules Were Preserved

All 33 pre-existing lines of `AGENTS.md` were preserved verbatim:
- The 16 core bullet rules (minimal Home, atomic edits, Windows-first, diff verification, untrusted web input, secret protection, STOP ALL killswitch) remain intact.
- The repository context ("Working on Prometheus", student owner, 8 GB RAM laptop baseline) was preserved unchanged.
- Operational guidelines (asking 2–4 questions first, simple features list, $1/$4 token limits) were preserved unchanged.
- Verification instructions (`pytest -q`, prompt hashes, `KEY_NAMES`, changelogs) remain untouched.

---

## 5. Existing Project License Findings

- **License File**: `LICENSE` at repository root.
- **License Type**: **MIT License** (Copyright (c) 2026 Reevias).
- **Metadata Declaration**: Declared as `"MIT"` in `pyproject.toml` (`license = "MIT"`).
- **Compatibility**: Permissive; allows modification, distribution, sublicensing, and commercial execution, provided the copyright notice is retained.

---

## 6. Third-Party Dependency and Licensing Concerns Discovered

Inspection of `pyproject.toml` revealed 21 core runtime dependencies and several optional groups. All dependencies are open source, but two specific licensing and architectural nuances require ongoing tracking:

1. **`fpdf2>=2.7` (LGPLv3)**:
   - *Status*: Used for PDF export.
   - *Consideration*: Under LGPLv3, dynamic linking/importing in Python does not infect the parent application provided `fpdf2` remains an unmodified third-party package. No proprietary relicensing risk exists, but modifications to `fpdf2` source code itself must be released under LGPLv3.
2. **`pywebpush>=2.0` (MPL 2.0)**:
   - *Status*: Used for Web Push notifications to phone app.
   - *Consideration*: File-level copyleft; safe for inclusion in MIT projects as long as pywebpush files themselves are unmodified.
3. **`pystray>=0.19` (Dual LGPLv3 / BSD)**:
   - *Status*: Optional desktop system tray icon. Safe under BSD clause.
4. **`pywebview>=5.0`, `fastapi>=0.115`, `uvicorn>=0.30`, `jinja2>=3.1`, `httpx>=0.27`, `cryptography>=42`**:
   - *Status*: Permissive (BSD-3-Clause, Apache-2.0, MIT). Fully compatible with MIT.
5. **No Mandatory Closed Runtimes**: The core runtime operates local-first without requiring closed SaaS backends.

---

## 7. Memory Architecture Overview

Prometheus 3.0 possesses a sophisticated, multi-tiered memory architecture implemented across `prometheus/memory/`:

```
prometheus/memory/
├── __init__.py      # Package exports
├── chunking.py      # Recursive hierarchical text splitter (parent-child chunking)
├── store.py         # SQLite storage engine, FTS5 index, vector BLOBs, RRF hybrid search
├── archive.py       # Chat conversation segmentation, topic clustering, and markdown mirror
├── facts.py         # Extracted user facts ("memories") and topic normalization
└── titles.py        # Algorithmic conversation titling and placeholder generators
```

Data flows into SQLite (`m.db`) through `Store`:
- Documents (web pages, user files, text notes) are stored in `documents`.
- Hierarchical chunks (parents and children) are stored in `chunks`.
- Full-text search is maintained via SQLite FTS5 `search_index`.
- Optional dense semantic vector embeddings are stored in `chunk_vectors`.
- Chat turns are segmented by `ChatArchive` into `chat_segments` and indexed as chunked documents under keys `chat:<id>:<range>`.

---

## 8. Chunking Implementation Status

**Status: FULLY IMPLEMENTED for text and documents; PARTIALLY IMPLEMENTED for AST/code structures.**

The core chunking engine is located in `prometheus/memory/chunking.py`. It implements a **Parent-Child Recursive Chunking** pattern:
- **Small Child Chunks (`CHILD_CHARS = 1000`, ~250 tokens)**: Designed for high-precision search matches (both BM25 word-level and vector embedding).
- **Large Parent Chunks (`PARENT_CHARS = 4000`, ~1000 tokens)**: Enclosing context surrounding the child chunks. When a child matches a search query, the parent chunk is retrieved so the LLM receives the complete conceptual context.
- **Overlap (`OVERLAP = 0.15`)**: 15% overlap between adjacent child chunks (calculated via `_with_overlap()` on word boundaries).

---

## 9. Chunking Configuration and Algorithms

Implemented in `prometheus/memory/chunking.py`:

```python
CHILD_CHARS = 1000      # about 250 tokens
PARENT_CHARS = 4000     # about 1000 tokens
OVERLAP = 0.15          # children share about 15% with the one before
MAX_DOC_CHARS = 2_000_000
SEPARATORS = ("\n# ", "\n## ", "\n### ", "\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")
```

### Algorithmic Mechanics:
1. **Recursive Splitting (`split_spans`)**:
   - Searches for the largest natural structural separator first.
   - Headings (`\n# `, `\n## `, `\n### `) are treated specially: `_cut_points` retains the heading at the start of the next piece (`keep_with_next = sep.startswith("\n#")`).
   - If a piece exceeds `size`, it falls back recursively to the next smaller separator (`\n\n` -> `\n` -> `. ` -> `, ` -> `" "`).
   - If a continuous string has no whitespace (e.g. continuous base64 or minified text), it falls back to a hard character slice (`split_spans` fallback line 71).
2. **Word-Boundary Overlap (`_with_overlap`)**:
   - Computes overlap starting offset `s = max(floor, a - overlap_chars)`.
   - Locates the nearest forward space `text.find(" ", s, a)` so chunk boundaries never bisect words.
3. **Safety Caps**:
   - Documents are sanitized and capped at `MAX_DOC_CHARS` (2,000,000 characters) in `chunk_text()`.
   - Null bytes (`\x00`) and carriage returns (`\r\n`) are normalized.

---

## 10. Metadata and Source-Tracking Findings

### Preserved Metadata:
In `prometheus/memory/store.py`:
- `documents` Table:
  - `id`: Auto-incrementing primary key.
  - `key`: Unique document identifier (e.g. `web:https://...`, `file:/path/to/doc`, `chat:<id>:<msg_range>`).
  - `title`: Human-readable title.
  - `source`: URL, filesystem path, or chat reference.
  - `private`: Boolean integer (0 or 1) indicating private/local-only status.
  - `hash`: SHA-256 digest of document body (`text_hash(text)`).
  - `chars`: Total character length.
  - `created` & `updated`: Unix timestamps.
- `chunks` Table:
  - `id`: Auto-incrementing chunk ID.
  - `doc_id`: Foreign key referencing `documents.id`.
  - `parent_id`: `NULL` for parent chunks; points to parent `chunks.id` for child chunks.
  - `seq`: Monotonic integer ordering sequence within document.
  - `text`: Chunk content.
  - `start` & `end`: Absolute character offsets in original source document.

### Source Traceability:
When `store.search_chunks()` executes, each returned `ChunkHit` dataclass retains:
- `doc_id`: Document ID.
- `title`: Source document title.
- `source`: Source URL or path.
- `private`: Access privacy flag.
- `match`: The exact child chunk text matched by BM25/Vector search.
- `context`: The enclosing parent chunk (`ptext`).
- `score`: Combined Reciprocal Rank Fusion (RRF) relevance score.

### Metadata Gaps:
- **No Project Identifier**: Neither `documents` nor `chunks` contains a `project_id` column.
- **No Extracted Heading Hierarchy**: Headings remain embedded in chunk text rather than parsed into separate structured metadata fields (e.g. `section_path = ["Chapter 1", "Section 2"]`).
- **Ephemeral IDs**: Chunk IDs are database auto-increments rather than deterministic content hashes.

---

## 11. Embedding and Indexing Findings

Prometheus implements a **hybrid multi-index architecture**:

1. **Keyword / Full-Text Indexing (FTS5)**:
   - Virtual Table: `search_index USING fts5(kind UNINDEXED, ref_id UNINDEXED, body, tokenize='porter unicode61')`.
   - Populated automatically on `store.add_document()` with `kind='chunk'`, `ref_id=cid`, `body=child.text`.
   - Ranked using BM25 (`ORDER BY bm25(search_index)`).
2. **Dense Vector Indexing (`chunk_vectors`)**:
   - Table: `chunk_vectors (chunk_id INTEGER PRIMARY KEY, model TEXT NOT NULL, vec BLOB NOT NULL)`.
   - Vectors stored as binary IEEE 754 32-bit floats (`array("f", vec).tobytes()`).
   - Embedder: `OllamaEmbedder` in `prometheus/models/ollama.py`.
   - Local-Only Security Guard: `OllamaEmbedder` validates that the URL is strictly localhost (`127.0.0.1` or `localhost`); raises `ValueError` if a remote URL is configured, preventing accidental data exfiltration.
   - Batching: `EMBED_BATCH = 16` in `_embed_chunks()`.
3. **Re-Indexing Behavior**:
   - Handled inside `store.add_document()`.
   - Compares SHA-256 `hash`. If identical, returns early without modifying database (idempotent).
   - If modified, calls `_drop_chunks(doc_id)`, updates `documents`, inserts new chunks and FTS records, and generates new embeddings.

---

## 12. Retrieval Findings

Retrieval is handled by `Store.search_chunks()` (`prometheus/memory/store.py:879`):

1. **Reciprocal Rank Fusion (RRF)**:
   - Executes `_word_ranks()` via FTS5 BM25.
   - Executes `_meaning_ranks()` via vector cosine similarity (filtered at `sim > 0.3`).
   - Fuses rankings using standard RRF formula ($k=60$):
     $$\text{score}(cid) = \sum_{m \in \{\text{word}, \text{vector}\}} \frac{1}{60 + \text{rank}_m + 1}$$
2. **Parent Deduplication**:
   - If multiple child chunks from the same parent match, only the highest-scoring child is returned (`seen_parents` set filter).
   - Prevents duplicate context windows from flooding LLM prompts.
3. **Context Assembly (`best_passages`)**:
   - Used by document agents to pack passages within an exact character budget (e.g. 4500 chars).
   - Re-sorts matched parent passages back into document reading order (`start_of` mapping) and separates them with `\n\n[…]\n\n`.
4. **Project Boundary Leakage**:
   - **Finding**: Because `documents` and `chunks` do not record `project_id`, `search_chunks(query)` queries the entire database across all indexed projects and files. Multi-project containment is currently unforced at the SQL query level unless `doc_ids` is pre-filtered.

---

## 13. Update and Deletion Behavior

- **Document Updates**: When `add_document` receives an existing `key` with a differing SHA-256 hash, it executes atomic chunk replacement:
  ```python
  self._drop_chunks(doc_id)
  self._db.execute("UPDATE documents SET ... WHERE id = ?", ...)
  ```
- **Document Deletions**: `delete_document(doc_id)` deletes chunks, FTS entries, and vector BLOBs in batches of 500 to prevent SQLite parameter overflow, followed by removing the document row.
- **Chat Deletions**: Chat messages are archived in topic segments. Deleting a chat currently leaves `chat:<id>:...` documents in `documents` unless `archive.sync` is invoked with cleanup.
- **Model Invalidation**: When `settings.embed_model` changes, queries to `chunk_vectors` filter by `WHERE v.model = ?`. Old vectors remain dormant, and new vectors are generated on subsequent document writes. There is no automated batch migration job.

---

## 14. Privacy and Security Findings

- **Private vs. Public Isolation**:
  - `documents.private` column enforces data isolation.
  - Documents flagged `private=True` (e.g. tax documents, financial records, local files) are strictly excluded from general search (`include_private=False` by default).
  - Cloud-facing planner prompts never receive private document chunks unless explicitly requested by a local-only tool.
- **Local-Only Embedding Enforcement**:
  - `OllamaEmbedder` rejects remote endpoints (verified by `test_embedder_refuses_remote_ollama`). Vector generation never leaks document contents to remote servers.
- **Untrusted Content Sanitization**:
  - Null bytes (`\x00`) and carriage returns are stripped in `chunk_text()`.
  - SQLite queries parameterized throughout; FTS MATCH expressions sanitized against query injection syntax in `_terms()`.

---

## 15. Test Results

The existing memory and chunking test suite was executed against the repository:

1. **`tests/test_v14_chunking.py`**:
   - Total Tests: **14**
   - Passed: **14 (100% PASS)** in 18.17s
   - Verified: Span coverage, heading preservation, child overlap, empty text handling, parent context retrieval, private chunk isolation, idempotent updates, best passages budget assembly, vector synonym matching, embedder error fallback, database migration, and remote host refusal.
2. **`tests/test_v14_chat_memory.py`**:
   - Total Tests: **10**
   - Passed: **10 (100% PASS)** in 20.06s
   - Verified: Topic segmentation, cross-chat linking, follow-up message grouping, context limit truncation, private chat isolation, chat rename synchronization, and memory directory containment.
3. **`tests/test_v12_memory.py`**:
   - Total Tests: **12**
   - Passed: **12 (100% PASS)** in 6.00s
   - Verified: FTS recall, word stemming, ranking, recency weighting, odd query handling (punctuations, emojis, boolean operators), and memory deletion.

**Total Verified Tests**: **36 passing tests** confirming that chunking and memory storage are functional, tested, and reliable.

---

## 16. Missing Capabilities

While functional for personal desktop use, the existing implementation has five notable missing capabilities:

1. **Multi-Project Workspace Containment (`project_id`)**:
   - Documents and chunks lack a workspace/project partition column. Searching chunks searches across all indexed documents.
2. **Code & AST Syntax Awareness**:
   - `chunking.py` splits markdown headings, paragraphs, and sentences. It does not parse Tree-Sitter AST nodes or preserve programming language functions, classes, and code fences (` ``` `) when code exceeds 4,000 characters.
3. **Structured Heading Hierarchy Metadata**:
   - Chunks contain heading text inside their body, but parent heading breadcrumbs (e.g. `["Architecture", "Memory Engine"]`) are not stored as structured index columns.
4. **Content-Addressable Chunk Hashing**:
   - Chunks use auto-incrementing integer IDs (`id INTEGER PRIMARY KEY`). Chunks lack deterministic content hashes (e.g. SHA-256 of chunk text), requiring full drop-and-reinsert during document updates.
5. **Embedding Model Migration Pipeline**:
   - No background re-vectorization job when switching from one embedding model to another.

---

## 17. Recommended Improvements (For Milestone M7/M8)

When implementing the advanced memory engine in Milestone M7/M8:
1. **Schema Migration**: Add `project_id TEXT NOT NULL DEFAULT 'global'` to `documents` and `chunks`, with compound index `(project_id, doc_id)`.
2. **AST-Aware Splitter**: Introduce `CodeChunker` utilizing Tree-Sitter (from Milestone M4) to split code files at function and class boundaries rather than character limits.
3. **Breadcrumb Metadata**: Add `section TEXT` column to `chunks` storing hierarchical heading paths.
4. **Deterministic Chunk Hashing**: Store `chunk_hash TEXT` to allow incremental diff updates (only embed new or modified chunks).
5. **Vector Re-Indexing Worker**: Provide a CLI command `prometheus memory reindex` to re-embed all documents when an embedding model is updated.

---

## 18. M2 Versus M8 Scope Boundaries

To prevent architectural drift and premature scope expansion:
- **Milestone M2 (Current Scope)**:
  - Focuses strictly on the Desktop Shell, UI workspace, persistent sidebar, quick actions, telemetry, and slash command palette.
  - UI may expose existing memory functionality (e.g. viewing documents, searching notes, listing memory topics on `/memory`).
  - M2 must **NOT** rewrite `chunking.py` or modify the SQLite schema in `store.py`.
- **Milestone M4 (Code Intelligence)**:
  - Introduces Tree-Sitter symbol parsing and repo profiling.
- **Milestone M7 / M8 (Advanced Memory & Sync)**:
  - Owns multi-layer memory, project-isolated namespaces, vector migrations, and distributed event replication.

---

## 19. Explicit Unresolved Questions

1. **Project Boundary Definition**: Should Prometheus 3.0 partition projects by filesystem path (e.g. git root directory) or by an explicit project identifier assigned in the UI?
2. **Default Embedding Model**: Should Prometheus package a lightweight CPU-optimized default embedding model (e.g. ONNX-quantized MiniLM via `fastembed`), or continue requiring local Ollama for vector capabilities?
3. **Code vs. Prose Splitter Selection**: Should chunker selection be determined by file extension, MIME type, or automatic content inspection?

---

## 20. Final Conclusion

1. **Open-Source Mandate**: Permanent global open-source requirements have been formally integrated into `AGENTS.md`, `PROMETHEUS_3_MASTER_PLAN.md`, and `ROADMAP.md`.
2. **Memory Chunking Audit Outcome**: Memory chunking is **already implemented and operational** in Prometheus 3.0 via `prometheus/memory/chunking.py` and `prometheus/memory/store.py`, backed by 36 passing unit tests.
3. **No Parallel Memory System**: Developing a second or parallel chunking engine would duplicate existing working code and violate repository rules. The existing implementation should be retained, surfaced via M2 UI where appropriate, and evolved into the M7/M8 multi-layer memory engine when scheduled.
