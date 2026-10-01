# the job father

This system is built on an open-source, locally hostable architecture designed to iteratively generate and refine tailored job application documents at minimal cost.

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Data Ingestion | Crawl4AI | Extracts raw job descriptions from URLs and converts web content into clean, LLM-ready Markdown. |
| Storage & Retrieval | PostgreSQL + pgvector | Stores embedded resume data and job postings, and performs semantic vector similarity search. |
| Orchestration | LangGraph + LangChain | Defines the agent workflow, managing application state and the cyclic routing between the drafting and self-correction nodes. |
| Intelligence | OpenAI `text-embedding-3-small` + `gpt-4o-mini` | Cost-effective embeddings and structured reasoning. |
| Deployment | Local CLI / Streamlit | Lightweight, free interface to interact with the agents. |

---

## Project status

| Layer | Status |
| --- | --- |
| Data ingestion (`web-crawler/`) | **Done (M0 + M1)** - built and tested |
| Storage - schema (`jobs`, `crawl_runs`, `crawl_failures`) | **Done** - DDL ready, not yet applied to this machine's cluster |
| Storage - role / database bootstrap | **To do** - needs the PostgreSQL superuser password (one time) |
| Storage - resume vectors (`resumes` table) | Not started |
| Orchestration (LangGraph) | Not started |
| Frontend (CLI / Streamlit) | Not started |
---


