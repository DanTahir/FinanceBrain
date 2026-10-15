# FinanceBrain demo video script (4–5 minutes)

This is the recording plan for the Graph Hacks 2026 submission video: a screen capture of the live site with a voiceover. It covers all three tracks in one pass:

- **T3**: company brain / knowledge graph
- **T1**: agentic reasoning and actions over graphs
- **T2**: agent memory and coordination

- **Live demo:** https://d1rnt6bwyb9n59.cloudfront.net
- **Repo:** https://github.com/DanTahir/FinanceBrain

All data shown is synthetic. Generators and storylines are described in [DATA_MODEL](DATA_MODEL.md); the ground truth lives in `data/raw/<tenant>/ground_truth.json`.

---

## Prep checklist

Do these before you press record.

- [ ] **Clean browser window.** Use a fresh profile or private window sized to **1440×900**, with browser zoom at **100%**. If the evidence graph looks cramped, use 90%. Close other tabs except the GitHub repo tab.
- [ ] **Open the live URL:** https://d1rnt6bwyb9n59.cloudfront.net.
- [ ] **Click Reset demo.** This restores `brain__halcyon` / `brain__brightwater` from the pristine snapshots (`GRAPH.COPY`) and rewinds the live feed to the 2026-10-01 cutoff.
- [ ] **Select Halcyon Ridge Capital** in the tenant switcher.
- [ ] **Open a second tab** on https://github.com/DanTahir/FinanceBrain, scrolled to the README architecture diagram. Optionally open a third tab on [`evals/results/SUMMARY.md`](../evals/results/SUMMARY.md).
- [ ] **Check the budget.** The demo budget meter shows the global daily budget ($25). One full rehearsal plus one take uses a small fraction of it. A blast-radius answer costs about $0.025. Avoid many repeated Opus runs on the same day. Over budget, the site serves cached guided answers only.
- [ ] **Know which questions are fast and which are slow.**

  | Question type | Examples | Behaviour on camera |
  |---|---|---|
  | Cacheable guided questions | crypto decision, PricingSvc, ReconLegacy, KYC, Falcon probe, Brightwater isolation | Stream quickly after a first run |
  | Non-cacheable questions | wire investigation, fraud rings, Falcon breach, memory | Always run live and take longer |

  Cacheable answers are cached per tenant, persona and live-feed position. Replaying the feed changes the position, so a post-replay question may run live the first time.
- [ ] **Pre-run once.** Do a full rehearsal so the cacheable answers are warm. Then click **Reset demo** again before the real take. Reset rewinds the feed, so do the rehearsal replay too, which re-warms the post-replay KYC answer at that position.
- [ ] **Plan to edit the wire wait.** The Investigator runs on Claude Opus 5.5, so the wire investigation can take about **30–60 s**. Keep the first tool calls on screen. Then speed up or trim the wait in editing, for example with a "≈40 s later" title card.
- [ ] **Record in a quiet room.** Read the voiceover column at a relaxed pace, about 150 words per minute.

---

## Segment 0: Hook and intro (0:00–0:25)

| # | Time | Screen / action | Voiceover (verbatim) | What it proves |
|---|---|---|---|---|
| 0.1 | 0:00–0:10 | Live site, **Chat** view, tenant **Halcyon Ridge Capital**. Slowly hover over the six view tabs: **Chat**, **Graph Explorer**, **Ingestion & Redaction**, **Investigations**, **Agents**, **Memory**. | "This is FinanceBrain: a multi-agent company brain for a finance firm. It answers questions with sources, investigates fraud, acts with human approval, and remembers." | One-line pitch; all three tracks in one app. |
| 0.2 | 0:10–0:25 | Point at the tenant switcher, the persona switcher and the **Try these** guided questions. | "Everything here is synthetic: emails, Slack, tickets, policies, meetings, CRM notes and a transaction ledger for two fictional companies. And FalkorDB is the only datastore. The graph, vector search, full-text search, agent memory, coordination, access control and even the encrypted PII vault all live in FalkorDB." | Synthetic data; FalkorDB as the single store. |

## Segment 1: T3 company brain (0:25–1:40)

| # | Time | Screen / action | Voiceover (verbatim) | What it proves |
|---|---|---|---|---|
| 1.1 | 0:25–0:45 | **Chat** view. Persona switcher → **Marcus Bell** (compliance officer). In **Try these**, click **Trace a decision** (*"Why did we stop onboarding crypto clients?"*). Let it stream. Hover a numbered citation. | "I'm Marcus, the Chief Compliance Officer. 'Why did we stop onboarding crypto clients?' The brain traces the decision back through the graph: a regulator email, a compliance ticket, a Slack debate, a meeting and the board memo. Each claim has a numbered citation." | Decision tracing; answers with sources (T3). |
| 1.2 | 0:45–0:55 | Scroll to the evidence graph. Then expand the collapsible tool / Cypher trace and show one Cypher query with its row count. | "Here's the evidence subgraph behind the answer. Every tool call is recorded with its exact Cypher, parameters and row counts. Only documents the tools actually returned count as citations." | Explainability: evidence subgraph and Cypher trace. |
| 1.3 | 0:55–1:05 | Persona switcher → **Kevin Zhao** (engineer). Click **Blast radius** (*"What breaks if PricingSvc goes down?"*). Show the dependency table. | "Now I'm Kevin, a junior engineer. 'What breaks if PricingSvc goes down?' A multi-hop dependency traversal: four systems at one hop, two at two hops, and ReconLegacy three hops out. It comes with owners and the people who actually know them." | Multi-hop traversal over DEPENDS_ON (T1/T3). |
| 1.4 | 1:05–1:15 | Click **Who knows what** (*"Who should I ask about ReconLegacy, and what is our risk if that person leaves?"*). | "'Who should I ask about ReconLegacy?' The formal owner isn't the real expert. FinanceBrain derives who knows what from authorship and mentions, with recency decay. Only one person really knows this system, so it's a bus-factor risk." | Who owns vs who knows (T3). |
| 1.5 | 1:15–1:25 | **Ingestion & Redaction** view. Click **Replay 3 days**. Click again until the live-feed date is past **Oct 6**. Show the new documents flowing through redact → extract → resolve → embed → write. Point at the redaction metrics and the canary scan showing **0 hits**. | "The company keeps changing, so the brain has to keep up. I'll replay the next few days of the live feed through the real pipeline. Each item is redacted, extracted, resolved, embedded and written. PII is pseudonymized before anything reaches the graph or any model. The canary scan checks every string in every graph for raw PII, and it's at zero." | Keeping knowledge current; privacy by construction. |
| 1.6 | 1:25–1:32 | In **Try the redactor**, paste a short made-up line with a fake phone number and email. Show the before/after with tokens like `⟨PHONE_0042⟩`. | "You can try the redactor yourself. Values become consistent tokens, so shared identifiers turn into graph structure without exposing the values." | Consistent pseudonymization. |
| 1.7 | 1:32–1:40 | Back to **Chat**, still **Kevin Zhao**. Click **Ownership over time** (*"Who owns the KYC pipeline now, and who owned it in August?"*). | "Now: who owns the KYC pipeline, and who owned it in August? Ownership moved to Compliance on October sixth. Facts are bitemporal, so old facts are superseded, never deleted, and both answers are right." | Bitemporal facts; knowledge stays current (T3). |

## Segment 2: T1 agentic reasoning and actions (1:40–3:10)

| # | Time | Screen / action | Voiceover (verbatim) | What it proves |
|---|---|---|---|---|
| 2.1 | 1:40–1:55 | **Chat** view. Persona switcher → **Nadia Petrov** (AML analyst). Click **Investigate a flagged wire** (*"Investigate the flagged $48k wire and tell me what we should do about it."*). Show the first tool calls streaming in the trace, such as `linked_accounts`, `detect_rings` and `find_path`. | "Track one: agents that reason and act. I'm Nadia, an AML analyst. 'Investigate the flagged forty-eight-thousand-dollar wire.' The Investigator runs on Claude Opus. It picks tools from our own MCP server, including linked accounts, ring detection and path finding, all graph algorithms exposed as tools." | MCP tool selection; graph algorithms as tools (T1). |
| 2.2 | 1:55–2:00 | *(Edit: speed up or cut the 30–60 s wait. Optional title card "≈40 s later".)* | *(no voiceover, or:)* "This takes a little while. It's doing real multi-step work." | — |
| 2.3 | 2:00–2:20 | Scroll to the evidence graph. Highlight five client nodes connected through shared PiiToken nodes (phone / address / device). Then trace the money path out to the exit counterparty. | "Here's the ring. Five clients who look unrelated, but they share the same pseudonymized phone numbers, addresses and devices. Because redaction produces consistent tokens, those shared identifiers become nodes in the graph. The agent found the ring without ever seeing a real phone number, and it followed the layered transfers out to the exit counterparty." | Multi-hop reasoning over pseudonymous structure (T1). |
| 2.4 | 2:20–2:35 | Show the end of the answer: case opened, proposed actions (freeze, SAR), and the handoff to Compliance. | "It doesn't just answer, it acts. It opened a case, attached the evidence, proposed account freezes and a suspicious activity report, and handed the case to the Compliance agent. Proposals only. A human has to approve." | Explainable actions; agent handoff. |
| 2.5 | 2:35–2:50 | Persona switcher → **Marcus Bell**. **Investigations** view. Open the new case: evidence and proposed actions. Click **Approve** on the freeze (and the SAR). | "Back as Marcus, in Investigations. Here's the case with its evidence and the proposed actions. Approval only happens here in the UI. That's the human in the loop." | Human-in-the-loop approval (T1). |
| 2.6 | 2:50–2:58 | **Agents** view. Show the handoff timeline (Investigator → Compliance) and the blackboard tasks. | "The Agents view shows how they coordinated: the handoff, and the tasks they created and claimed on a shared blackboard, which is also in the graph." | Shared state and handoffs (T1/T2). |
| 2.7 | 2:58–3:04 | **Chat**. Persona switcher → **Kevin Zhao**. Click **Try to get past the wall** (*"What is Project Falcon and who is working on it?"*). | "Now an information barrier. Kevin isn't on the Falcon deal team. 'What is Project Falcon?' He gets nothing. The access check runs inside the Cypher query, so restricted documents never reach the model." | ACL enforced in Cypher; no MNPI leak. |
| 2.8 | 3:04–3:10 | Persona switcher → **Marcus Bell**. Click **Information-barrier breach** (*"Has Project Falcon leaked outside the deal team?"*). | "Compliance sits above the wall. 'Has Project Falcon leaked outside the deal team?' Yes. It finds the email where a deal team member sent deal details to an outsider, and that surveillance read is audit-logged." | Barrier-breach detection (T1/T3). |

> If the feed has been replayed past Oct 7, the breach answer may also surface the repeat leak in Slack. That is a nice bonus beat, but don't script it as guaranteed.

## Segment 3: T2 memory and coordination (3:10–4:10)

| # | Time | Screen / action | Voiceover (verbatim) | What it proves |
|---|---|---|---|---|
| 3.1 | 3:10–3:20 | **Chat**. Persona switcher → **Nadia Petrov**. Click **Teach it a preference** (*"Remember that I prefer answers as short bullet points."*). | "Track two: memory. As Nadia: 'Remember that I prefer answers as short bullet points.'" | Semantic memory write. |
| 3.2 | 3:20–3:35 | Start a new chat. Click **Pick up where we left off** (*"What were we working on last time, and what is still open?"*). Show the bullet-point answer referencing the wire case. | "New session. 'What were we working on last time, and what's still open?' It remembers the wire investigation and the open case, and it answers in bullet points, as I asked." | Episodic memory across sessions; preferences applied. |
| 3.3 | 3:35–3:50 | **Memory** view. Show sessions, facts, playbooks and the **Forget** control. Optionally click **Procedural memory** (*"Which playbook do we follow for a suspicious wire, and has it been used before?"*). | "The Memory view shows everything it knows about me: sessions, facts and playbooks the Librarian agent learned from earlier investigations. I can forget any of it. Each visitor gets a private memory graph per tenant and persona, so my memory is never mixed with anyone else's." | Episodic, semantic and procedural memory; user control; per-user isolation. |
| 3.4 | 3:50–4:02 | Tenant switcher → **Brightwater Credit Union**, persona **lender**. Click **Tenant isolation** (*"What is Project Falcon?"*). | "Different company, different graph. Over at Brightwater Credit Union: 'What is Project Falcon?' It doesn't exist here. Each tenant is its own graph, and our isolation and auth checks pass a hundred and fifty-five out of a hundred and fifty-five." | Tenant isolation (T2). |
| 3.5 | 4:02–4:10 | Stay on screen, or briefly show the repo's eval summary. | "And it's built for concurrency. On a laptop, eight writers and sixteen readers on one shared graph sustained about two hundred fifty write batches and nearly eight hundred reads a second, at a p95 of twenty-eight milliseconds." | High concurrency (T2). |

## Segment 4: Architecture close (4:10–4:40)

| # | Time | Screen / action | Voiceover (verbatim) | What it proves |
|---|---|---|---|---|
| 4.1 | 4:10–4:22 | Switch to the GitHub repo tab. Scroll the README architecture diagram. Point at the MCP server box. | "Under the hood: FastAPI and seven agents, all reaching the graph through our own MCP server with thirty-one tools. The same server works from Claude Desktop with a read-only token you mint from the live site." | MCP tool layer; open architecture. |
| 4.2 | 4:22–4:32 | Open [`evals/results/SUMMARY.md`](../evals/results/SUMMARY.md). Highlight redaction recall and isolation results. Briefly show the CloudFront / EC2 part of the diagram. | "We measured it. Redaction recall is a hundred percent on the injected PII, the canary scan finds zero leaks, and all isolation checks pass. Thirty-eight of thirty-nine end-to-end golden questions pass, judged by Opus. It's deployed on AWS behind CloudFront, with FalkorDB never exposed to the internet." | Evaluated, deployed, secure. |
| 4.3 | 4:32–4:40 | Back to the live site, **Chat** view, Halcyon. | "FinanceBrain: one FalkorDB graph that knows your company, reasons over it, acts with your approval, and remembers. Try it at the link below." | Closing line. |

---

## Backup plan

- **A live run is slow.** Cut or speed up the wait in editing; the wire investigation on Opus routinely takes 30–60 s. For the other beats, prefer the cacheable guided questions, which stream fast once warmed:
  - Trace a decision
  - Blast radius
  - Who knows what
  - Ownership over time
  - Try to get past the wall
  - Tenant isolation
- **A run errors or goes off-script.**
  1. Click **Reset demo**.
  2. Re-select **Halcyon Ridge Capital** and the right persona.
  3. Re-run the guided question.

  Record segments as separate takes so one failure doesn't cost the whole video.
- **The budget meter is near the limit.** Over budget, the site serves only cached guided answers. Non-cacheable beats won't run live. Those are the wire investigation, fraud rings, Falcon breach and the memory questions. Use a take recorded earlier, or record the next day after the daily budget resets.
- **The live site is unreachable.** Run the stack locally with the repo's Docker Compose setup and the offline seed from the committed extraction/embedding cache (`data/extracted`). Record from localhost with the same clicks. Note in the video description that the live URL is the same build.
- **Rate limited.** Per-IP limits are 20 chat requests and 40 ops requests per 10 minutes. Wait out the window between rehearsal and take, or space the takes out.

## Shot list summary

**Intro**
- [ ] Intro: the six view tabs, tenant switcher, persona switcher, **Try these**
- [ ] Voiceover says "all data is synthetic" and "FalkorDB is the only datastore"

**T3: company brain**
- [ ] Crypto decision answer with numbered citations (Marcus Bell)
- [ ] Evidence subgraph
- [ ] Expanded Cypher trace with a query and its row counts
- [ ] PricingSvc blast-radius table with 1/2/3-hop dependents (Kevin Zhao)
- [ ] ReconLegacy: who knows vs who owns, bus-factor risk
- [ ] **Ingestion & Redaction**: Replay past Oct 6, pipeline stages visible
- [ ] Redaction metrics and canary scan at **0 hits**
- [ ] **Try the redactor** before/after with `⟨…⟩` tokens
- [ ] KYC ownership now vs August

**T1: agentic reasoning and actions**
- [ ] Wire investigation tool calls in the trace (Nadia Petrov)
- [ ] Fraud ring in the evidence graph via shared PiiToken nodes
- [ ] Case opened; freeze and SAR proposed; handoff to Compliance
- [ ] **Investigations**: **Approve** clicked (Marcus Bell)
- [ ] **Agents**: handoff timeline and blackboard tasks
- [ ] Falcon probe refused (Kevin Zhao)
- [ ] Falcon leak found (Marcus Bell)

**T2: memory and coordination**
- [ ] Preference remembered (Nadia Petrov)
- [ ] New session recalls the previous work in bullet points
- [ ] **Memory** view: sessions, facts, playbooks, **Forget**
- [ ] Brightwater "What is Project Falcon?" returns nothing
- [ ] Load-test numbers in the voiceover

**Architecture close**
- [ ] README architecture diagram and MCP server (31 tools, Claude Desktop)
- [ ] Eval summary: redaction recall, canary, 155/155 isolation
- [ ] AWS CloudFront deploy
- [ ] Closing line on the live site

## Submission reminder

- **Name all three tracks** in the submission and the video description:
  - **T1**: Agentic reasoning and actions over graphs
  - **T2**: Agent memory and coordination
  - **T3**: Company brain / knowledge graph
- **Links to include:**
  - Repo: https://github.com/DanTahir/FinanceBrain
  - Live demo: https://d1rnt6bwyb9n59.cloudfront.net
  - Demo video: *(add the uploaded video URL)*
- Point judges to the [eval results](../evals/results/SUMMARY.md), including [golden questions](../evals/results/golden.md), and to [DATA_MODEL](DATA_MODEL.md) for the graph design.
- Deadline: **Sun Oct 18 2026, 11:29 AM PDT.**