# Node 2: Resume Retrieval (RAG)

Node 2 of the job-application agent. Given a job posting, it finds the parts of a
master resume that fit the job best and returns them for the Writer node (Node 3).

```
job posting (jobs table row or text)  →  Node 2  →  best-matching resume chunks
```

This node is the **retrieval** half of RAG. It does not write anything. Writing the
resume and cover letter is done by later nodes, using the chunks returned here.

## How it works

1. **Ingest (once, and again when the resume changes).** The Markdown files in
   `master_resume/` are cut into chunks (2 bullets per chunk by default). Each chunk
   keeps its project header (title, type, organisation, skills) so it makes sense on
   its own. Each chunk is embedded with OpenAI `text-embedding-3-small` and stored in
   the `resume_chunks` table (PostgreSQL + pgvector).
2. **Retrieve (for each job).** The job text is embedded, and pgvector finds the
   chunks with the highest cosine similarity. A small bonus (0.03 per shared skill,
   capped at 0.15) is added for skills that the job and the chunk both list. The top
   chunks are returned.

Retrieval runs through LangChain (`OpenAIEmbeddings` and `PGVectorStore`). Reading the
job row, the skills bonus and the printing are plain Python.

## Files

| File | Purpose |
|---|---|
| `retriever_lc.py` | **Main retriever** (LangChain). Job in, ranked chunks out. |
| `retriever.py` | Same retrieval in plain SQL. Kept as a baseline; `retriever_lc.py` reuses its helpers. |
| `ingest_resume.py` | Chunks `master_resume/*.md`, embeds, and stores in `resume_chunks`. |
| `embed_jobs.py` | Backup: fills in `jobs.embedding` for postings that have none. |
| `db_init.py` | Applies the setup files in `../database/migrations/`. |
| `config.py` | Settings and shared helpers. |
| `master_resume/` | Source resume files (personal; one file per project or role). |
| `sample_jobs/` | Test job postings and `expected_results.md`. |
| `.env.example` | The settings the code reads. |

The table definition is in `../database/migrations/003_resume_chunks.sql`.

## Setup

Requirements: Python 3.10+ (developed on 3.14), PostgreSQL 16 with the pgvector
extension, and an OpenAI API key.

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then fill in your own key and database password
```

Start PostgreSQL (for example `..\database\scripts\pg.ps1 start`), then create the tables:

```powershell
.\.venv\Scripts\python.exe db_init.py
```

## Usage

Load the resume into the database:

```powershell
.\.venv\Scripts\python.exe ingest_resume.py --dry-run     # preview, writes nothing
.\.venv\Scripts\python.exe ingest_resume.py               # real run (needs OPENAI_API_KEY)
.\.venv\Scripts\python.exe ingest_resume.py --status      # what is stored
```

Find the best resume chunks for a job:

```powershell
.\.venv\Scripts\python.exe retriever_lc.py --latest                 # newest row in jobs
.\.venv\Scripts\python.exe retriever_lc.py --job-id 42 --top-k 8
.\.venv\Scripts\python.exe retriever_lc.py --text "AI Engineer, RAG, Python"
.\.venv\Scripts\python.exe retriever_lc.py --latest --json          # what the Writer receives
```

Settings are read from `.env`: `OPENAI_API_KEY`, `DATABASE_URL`, `EMBEDDING_MODEL`,
`EMBEDDING_BATCH_SIZE`, `RETRIEVAL_TOP_K`.

## Output (for the Writer node)

```json
{
  "job": {"id": 1, "title": "...", "company": "...", "skills": ["..."]},
  "chunks": [
    {
      "source_file": "policy_rag_chatbot.md",
      "section": "Internal Policy RAG Chatbot",
      "chunk_index": 1,
      "content": "text of the chunk",
      "skills": ["Python", "RAG"],
      "score": 0.476,
      "matched_skills": ["python", "rag"],
      "boost": 0.06,
      "final_score": 0.536
    }
  ]
}
```

`final_score` (similarity plus skills bonus) decides the order. `score` is the pure
similarity.

## Testing

`sample_jobs/` holds real job postings, and `expected_results.md` records which resume
chunks each one should return. On these samples, an AI automation job returned the
chatbot and AI skills chunks, a data analyst job returned the data internship and
data skills chunks, and a job unrelated to the resume produced the weakest scores.
Changes to chunking or ranking should be checked against the same set.

## Known limits

- Skills matching compares exact words, so "LLM" does not match "LLM applications".
- The skills-summary chunks have no skills list, so they cannot earn the bonus.
- The job text is embedded again at query time. The stored `jobs.embedding` is not used.
- Raw scores are not comparable between different jobs, so there is no fixed minimum
  score for a "good match".
- Chunk text is sent to OpenAI to be embedded. Do not put contact details or
  confidential information in `master_resume/`.
- Tested on hand-added job rows only. Not yet tested on scraped postings.

## Status

Retrieval is working and tested. Next: wrap `retrieve_lc` as a LangGraph node and
test on postings from the Node 1 scraper.