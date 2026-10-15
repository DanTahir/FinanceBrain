# data/

**Everything in this folder is SYNTHETIC.** No real people, clients, accounts, or communications.

- `raw/` - generated corpus per tenant (JSONL), including PII span ground truth for the redaction eval.
- `extracted/` - cached LLM extraction output and embeddings (keyed by model ID), so seeding is deterministic.
