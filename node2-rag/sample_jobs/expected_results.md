# Expected results

## ai_automation_engineer.txt
The job is about building AI-powered automation for business teams: LLM
integration, APIs, Python, workflow automation, and working with non-technical
staff.

Should appear in the top 6:
- Policy chatbot: the backend / Gemini / system prompt chunk
- Policy chatbot: the deployment / Python scripts / regression test chunk
- AppSheet: the workflow chunk (approval workflow, email bots, replaced manual
  processes, used across departments)
- At least one Skills chunk (AI/GenAI and cloud tools, or Python and automation)

Nice to have:
- Policy chatbot: the chunk about working with the frontend developer and
  policy owners (shows working with other teams)
- AppSheet: the data model chunk

Should NOT appear:
- Wazuh project
- VAPT coursework
- MDEC internship
- Education and activities

## data_analyst.txt
Should appear in the top 6:
- MDEC internship chunks (Power BI dashboard, Alteryx and Excel data cleaning)
- Skills: Data and BI chunk (Power BI, Alteryx, Excel, MySQL)
- Skills: Programming chunk (Python, SQL)
- Education chunk (Computer Science degree)
Nice to have: AppSheet data model chunk (relational data model)
Should NOT be near the top: Wazuh, VAPT

## ai_ml_engineer.txt
Note: this job wants model training, computer vision, PyTorch and TensorFlow.
My resume has LLM and RAG work but no model training. A real gap.
Should appear: chatbot chunks, AI/GenAI skills chunk, Programming skills chunk,
the cloud deployment chunk, the education chunk
Should NOT be near the top: Wazuh, VAPT
Expect scores LOWER than for the RAG-style jobs. That is the correct outcome.

## marketing_manager.txt
Note: this job does not fit my resume at all.
Expect NO strong match. The best chunks are probably AppSheet or the chatbot
stakeholder chunk.
Key check: the top score should be clearly lower than for the fitting jobs
(my guess is below about 0.35).

## Baseline (top 6, text search, no skills boost)
- ai_automation: AppSheet chunks missed top 6 (7th, 8th). Chatbot took 4 of 6 slots.
- data_analyst: matches expectations. Python/SQL chunk at 6th.
- ai_ml_engineer: MDEC (0.45) outranks the chatbot (0.37-0.40).
  Programming chunk not in top 8.
- marketing_manager: top score 0.387 (lowest of the four).
  A fixed score cut-off won't work, since scores vary by job.