This system is built on an open-source, locally hostable architecture designed to iteratively generate and refine tailored job application documents at minimal cost.

Data Ingestion: Crawl4AI extracts raw job descriptions from URLs and converts the web content into clean, LLM-ready Markdown.

Storage & Retrieval: A local PostgreSQL database with the pgvector extension stores your embedded resume data and performs semantic vector similarity searches.

Orchestration: LangGraph and LangChain define the agent's workflow, managing application state and the cyclic routing between the drafting and self-correction nodes.

Intelligence: OpenAI's text-embedding-3-small and GPT-4o-mini models provide highly capable, cost-effective processing for embeddings and structured reasoning.

Deployment: A local CLI or Streamlit application serves as a lightweight, free interface to interact with the agents.
