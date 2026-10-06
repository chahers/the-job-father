# Internal Policy RAG Chatbot
Type: Work project
Organisation: MRCB (AI Engineer, Nov 2025 – Present, 1-year contract)
Skills: Python, RAG, Google ADK, Gemini Flash, Vertex AI Search, Vertex AI Agent Engine, prompt engineering, regression testing, cloud deployment

- Built the Python backend for an internal RAG chatbot over 60+ policy PDFs using Google ADK, Gemini Flash and Vertex AI Search; in live pilot with HR, Procurement and Operations.
- Wrote a structured system prompt that limits answers to retrieved documents, adds a fallback, and enforces citations to reduce hallucination; answers stream to a custom web UI.
- Deployed to Vertex AI Agent Engine with Python scripts, managing configuration and authentication with environment variables and gcloud credentials; kept credentials out of Git.
- Built a regression test suite (YAML ground-truth questions, CSV accuracy reports) and a latency diagnostic script.
- Fixed a UI freeze (11s backend vs. 10s frontend timeout) by capping agent iterations, and restored dropped citations by restructuring the prompt; worked with a frontend developer, policy owners and my manager.
- Not all of the policy documents were being parsed successfully, so some policies could not be retrieved; I worked through this to improve coverage.
- Several policy folders had overlapping content, which made getting accurate answers difficult; prompt design was a major part of resolving this. 