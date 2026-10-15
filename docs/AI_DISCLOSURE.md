# AI Disclosure

This document covers how AI was used to build FinanceBrain and how it is used inside the product. It addresses the Graph Hacks 2026 rules on disclosing AI-assistant use (rules 11–13), including the requirement that the team understands the code it submits.

## Summary

FinanceBrain was built during the hackathon window by **Dan Tahir** (solo team, GitHub [DanTahir](https://github.com/DanTahir)) working with an AI coding agent named **Klenny**, which runs on Anthropic Claude models. Klenny generated most of the code, infrastructure scripts, synthetic-data generators and documentation. Dan directed and reviewed that work. He set the requirements, approved the plan and the major decisions, and is responsible for what was submitted.

At runtime the product itself uses Claude models and an embedding model through OpenRouter. The synthetic dataset was also largely LLM-written, under the privacy constraints described below.

## Division of work

| Area | Human (Dan Tahir) | AI coding agent (Klenny) |
|---|---|---|
| Requirements & scope | Defined goals, tracks entered and the demo storylines | Drafted the implementation plan for approval |
| Key decisions | Approved the plan and key decisions, e.g. OpenRouter instead of Bedrock and the data/privacy policy (synthetic-only data, pseudonymize before any LLM call) | Proposed options and trade-offs, and documented them |
| Code | Reviewed key modules | Wrote the backend, frontend, infrastructure, data generators and evals |
| Execution | Set the agent's approval settings | Ran tests, evals and deploy scripts through tool calls, within those approval settings |
| AWS | Owned the AWS account and operated the credentials | Wrote the idempotent AWS CLI scripts (`infra/*.sh`) |
| Validation | Tested the live demo at https://d1rnt6bwyb9n59.cloudfront.net | Wrote the automated tests and the eval harness |
| Docs & submission | Recorded the demo video and voiceover, and made the submission | Wrote the documentation, including this file |

## AI used at runtime (inside the product)

All LLM and embedding calls go through **OpenRouter** using the `openai` SDK. The model slugs are set in `backend/config.py`.

| Model | Role in FinanceBrain | Provider |
|---|---|---|
| `anthropic/claude-opus-5.5` | Investigator agent (fraud, AML, information barrier), golden-question eval judge, storyline authoring | OpenRouter |
| `anthropic/claude-sonnet-5.5` | Concierge, Brain, Compliance and Comms agents, synthetic content writers | OpenRouter |
| `anthropic/claude-haiku-5.5` | Ingestion extraction, Sentinel agent, Librarian agent | OpenRouter |
| `voyageai/voyage-4` | Embeddings (1024 dimensions, cosine vector index in FalkorDB) | OpenRouter |

Requests use the `data_collection: deny` provider preference, which routes only to providers that do not retain or train on prompts. No `:free` model variants are used.

Every outbound LLM or embedding payload has already been pseudonymized. An outbound guard blocks any call whose payload contains a raw PII pattern (see [Safeguards](#safeguards)).

## AI used to create the synthetic dataset

- **All data is synthetic.** No real personal data and no restricted personal data appear anywhere in the corpus, the graph, the evals or the demo.
- **LLMs wrote the content from placeholder-only prompts.** Claude models wrote the emails, Slack messages, tickets, documents, meeting transcripts and CRM notes. Prompts contained placeholders only, so no PII values were ever sent to an LLM.
- **Values were injected locally.** Names, phone numbers, addresses, account numbers and similar values were generated locally with Faker and inserted into the text afterwards.
- **Ground truth was recorded.** Storyline facts and every injected PII span were saved to `data/raw/<tenant>/ground_truth.json`. The redaction, graph-check, retrieval and golden-question evals score against this file.
- **Seeding needs no LLM calls.** The extraction and embedding cache is committed in `data/extracted`, so seeding is deterministic and costs nothing in LLM usage.

## Verification & understanding

The rules require the team to understand the submitted code. The following support that:

- **Automated tests:** 79 pytest tests pass, including regression tests for issues found in production, such as `tests/test_db_config.py` for the redis-py socket timeout.
- **Eval suite:** `evals/` covers redaction recall and precision, storyline graph checks, tenant isolation and information barriers, retrieval quality, load, and golden questions. See the [eval summary](../evals/results/SUMMARY.md) and the [golden-question results](../evals/results/golden.md).
- **Architecture walkthrough:** [ARCHITECTURE](ARCHITECTURE.md) is written so the owner can explain every component and every query path, from redaction through extraction, resolution, embedding and graph writes to agent tool calls.
- **Centralised Cypher:** all Cypher lives in `backend/graph/queries.py`, and each query is documented in [CYPHER](CYPHER.md). Nothing is hidden in ad-hoc query strings.
- **Visible queries in the UI:** every tool call records its Cypher, parameters, row counts and returned ids. These appear in the Chat view's trace panel, so the query behind any answer can be inspected.
- **Public history:** the full commit history is public at https://github.com/DanTahir/FinanceBrain and starts after the hackathon kickoff.

## Safeguards

- **Secrets are never committed.**
  - A pre-push secret scan (`scripts/check_secrets.sh`) checks outgoing commits for credentials.
  - The local key file is gitignored.
  - In production, all secrets live in AWS SSM Parameter Store as SecureString values. This covers the OpenRouter key, the FalkorDB password, the origin secret, the HMAC signing keys and the demo MCP token.
  - The PII token vault's data key is wrapped by AWS KMS.
- **PII guard on outbound calls.** PII is pseudonymized with Presidio and custom finance recognizers before anything reaches the graph or any LLM or embedding call. The outbound guard then blocks any payload that still contains raw PII patterns.
- **Canary scan.** A canary scan checks every string property of every graph for raw PII. It found 0 hits across 127,800 strings on the production seed.
- **Bounded runtime AI.** Agents are limited to 8 tool steps. Usage is capped by per-IP rate limits and a global daily budget. Any action proposed by an agent (freeze, SAR, notify, restrict) needs human approval in the UI before it takes effect.