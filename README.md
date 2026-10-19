# AI Knowledge OS (AI Library)

> An open-source, local-first knowledge operating system and 3D epistemic memory gateway for humans and AI agents.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12+-green.svg)](pyproject.toml)
[![MCP: Ready](https://img.shields.io/badge/MCP-Protocol%20Ready-purple.svg)](https://modelcontextprotocol.io)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)](backend/)
[![Three.js](https://img.shields.io/badge/Frontend-Three.js-black.svg)](frontend/)

---

## 🌟 Overview

**AI Knowledge OS** bridges the gap between fragmented personal notes, conversational AI transcripts, and long-term agent memory. It runs locally via SQLite and FastAPI, provides a real-time **Three.js 3D interactive celestial universe** in the browser, and exposes standard **Model Context Protocol (MCP)** interfaces to connect AI assistants (such as Claude Desktop, Cursor, Antigravity, and WorkBuddy).

Unlike naive vector-only dumps, AI Knowledge OS enforces **epistemic governance**:
- **Strict Authorship & Lineage**: Distinguishes between human user notes, raw imported chat sources, and inferred AI memories.
- **Candidate Approval & Auto-Sync**: Inferred decisions and memories are staged as candidates before entering canonical memory.
- **Hybrid Retrieval (RRF)**: Reciprocal Rank Fusion combining SQLite FTS5 BM25 keyword search with dense vector embeddings.
- **Multi-Agent Handoffs**: Standardized work-handoff protocol (`create_work_handoff`, `acknowledge_work_handoff`) to share context across disparate agents.

---

## 🌌 3D Universe Visualization

The web interface transforms your knowledge graph into an interactive solar system:
- **Central Star (Sun)**: Topic clusters and domains.
- **Planets & Orbitals**: Individual content nodes (Notes, Memories, Project Documentation).
- **Spider-Mecha Inspection HUD**: Real-time drill-down into node revisions, evidence citations, and conflict audits without leaving the 3D space.
- **Cockpit Flight Navigation**: First-person and third-person orbital camera flight modes to explore large-scale knowledge spaces.

---

## 🛠️ Key Capabilities

| Layer | Features |
|---|---|
| **Core Storage** | SQLite local database with FTS5, JSONB support, and optimistic concurrency versioning. |
| **Hybrid Retrieval** | BM25 full-text search + dense vector index blended with reciprocal rank fusion (RRF). |
| **Epistemic Engine** | Candidate reviews, conflict detection, quarantine workflows, and audit trails. |
| **MCP Integration** | Native Stdio & SSE servers exposing 20+ specialized knowledge governance tools. |
| **Multi-Agent Bridge** | Work handoffs, desktop client configurations, and cross-session trajectory imports. |
| **3D Interface** | Pure Three.js + WebGL visual workspace with GLTF celestial models and cybernetic HUDs. |

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.12 or higher
- [uv](https://docs.astral.sh/uv/) (recommended) or standard `pip`

### 2. Installation & Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/GGJustME/ai-library.git
cd ai-library

# Using uv (fastest)
uv sync

# Or using standard pip
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### 3. Initialize the Database

Initialize an empty knowledge store from the canonical schema:

```bash
python scripts/manage_snapshots.py --init
```

### 4. Launch the Web UI & API Server

Start the local FastAPI backend (which also serves the 3D frontend):

```bash
python -m uvicorn backend.adapters.api.app:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser at `http://127.0.0.1:8000` to view the 3D Universe.

---

## 🔌 Connecting AI Agents via MCP

AI Knowledge OS ships with a standard Model Context Protocol (MCP) server.

### Claude Desktop Configuration
Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ai-memory-library": {
      "command": "python",
      "args": [
        "-m",
        "backend.adapters.mcp.stdio_server"
      ],
      "cwd": "/path/to/ai-library"
    }
  }
}
```

### Antigravity / Cursor Configuration
In your project MCP configuration:

```json
{
  "name": "ai-memory-library",
  "command": "python",
  "args": ["-m", "backend.adapters.mcp.stdio_server"],
  "cwd": "${workspaceFolder}"
}
```

### Core MCP Tools Exposed

- `search_knowledge_context`: Hybrid BM25/vector search across authorized knowledge scopes.
- `get_memory_node`: Retrieve a specific knowledge node with citations and revision history.
- `create_user_note` / `revise_user_note`: Create and update immutable human user notes.
- `list_candidates` / `accept_candidate`: Review and promote inferred agent conclusions into canonical memory.
- `create_work_handoff` / `get_work_handoff`: Pass task context and state safely across multiple agent sessions.
- `begin_spider_session` / `drive_spider`: Programmatically drive the 3D mecha inspector to examine graph neighborhoods.

---

## 📁 Repository Structure

```text
ai-library/
├── backend/                   # Python FastAPI service & MCP server
│   ├── adapters/              # MCP (stdio/sse) and REST API adapters
│   ├── core/                  # Hybrid retrieval (BM25, vector, RRF) & evidence
│   ├── candidates/            # Candidate memory lifecycle & approval engine
│   ├── conflicts/             # Conflict detection & resolution workflows
│   ├── handoffs/              # Cross-agent work handoff management
│   ├── storage/               # SQLite canonical store and raw sources engine
│   └── schema/                # Data contracts, migrations & models
├── frontend/                  # WebGL 3D Universe visual workspace
│   ├── index.html             # Main 3D viewport & HUD
│   ├── js/                    # Three.js celestial engine & state management
│   ├── css/                   # Cybernetic styling & HUD themes
│   └── assets/                # Procedural and GLTF celestial models
├── plugins/                   # Integration plugins for external harnesses (e.g. DeepSeek)
├── scripts/                   # Utility scripts for indexing, maintenance, and imports
├── tests/                     # Automated unit and browser verification tests
├── docs/                      # Technical specifications and design plans
├── pyproject.toml             # Project dependency definition
└── README.md                  # Project overview
```

---

## 🛡️ License

This project is licensed under the [MIT License](LICENSE).
