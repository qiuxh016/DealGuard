# Independent policy retrieval challenge author brief

## Purpose

Create a bilingual challenge set to evaluate whether DealGuard retrieves policy evidence only when the customer's request is actually within the policy's intent. The author must not have worked on the current retrieval vocabulary, BM25 scorer, or existing challenge cases.

## Materials the author may receive

- The versioned policy source documents used by the evaluation fixture, without previous queries, labels, score reports, scorer code, vocabulary tables, or retrieval thresholds.
- This brief and the required output schema below.

Do not share `data/policy_rag_safety/crosslingual_challenge_cases.jsonl`, its answer labels, baseline metrics, or implementation details with the author before their files are sealed.

## Dataset requirements

- Write 50–80 natural customer inquiries in English, Chinese, and mixed Chinese/English. Do not translate the existing evaluation inquiries or copy policy wording as the only signal.
- Cover direct policy requests, colloquial paraphrases, abbreviations, incomplete wording, and near-topic requests whose intent is unrelated to policy eligibility or commercial terms.
- Include multiple policies and policy clauses. Include cases where the correct outcome is to cite one precise clause and cases where no policy clause is relevant.
- Keep each inquiry realistic and self-contained. Do not put the expected policy ID or citation in the query.
- Store queries and answer labels separately. The answer key must identify policy relevance, expected policy/clause for relevant cases, and the reason for abstention on irrelevant cases.

## Sealing and evaluation sequence

1. Before seeing any scores, the author provides `queries.jsonl` and a separate `answer_key.jsonl`.
2. Record SHA-256 hashes for both files and the frozen scorer (`backend/app/rag/evaluation.py`) before joining query IDs to answer labels.
3. The evaluator records the baseline report and hashes before any scorer or prompt changes.
4. Resolve label disagreements with the author and record any adjudication before unsealing results.
5. Keep all examples and labels fixed during the first evaluation. Any later tuning requires a new version and a fresh holdout set.

## Suggested fields

`queries.jsonl` contains no relevance labels or expected citations:

```json
{"case_id":"author-001","query":"..."}
```

`answer_key.jsonl` contains one row per query:

```json
{"case_id":"author-001","policy_relevant":true,"expected_action":"cite","target_policy_id":"...","target_version":"v1","expected_citation_index":0,"intent_category":"...","language":"zh-en"}
```

`expected_action` is `cite` or `abstain`; relevant queries may still require abstention if the request does not support a reliable citation. For irrelevant cases, set `policy_relevant` to `false` and `expected_action` to `abstain`. Keep any rationale in a separate adjudication file; do not place answers in the query file.

## Hash-first evaluation commands

Before scoring, seal both data files plus the policy corpus and scorer hashes. Archive or commit the seal file before running the evaluation command:

```powershell
.\backend\.venv\Scripts\python.exe scripts\evaluate_policy_rag_sealed.py seal `
  --queries path\to\queries.jsonl `
  --answer-key path\to\answer_key.jsonl `
  --output docs\independent-challenge-seal.json

.\backend\.venv\Scripts\python.exe scripts\evaluate_policy_rag_sealed.py evaluate `
  --queries path\to\queries.jsonl `
  --answer-key path\to\answer_key.jsonl `
  --seal docs\independent-challenge-seal.json `
  --output docs\independent-challenge-results.json
```

The evaluator refuses to score if either data file, the policy fixture, or scorer differs from the seal. It validates unique/matching case IDs and reports overall, per-language, and per-intent metrics without returning query text.

## Interpretation boundary

This is an independent challenge set, not automatically a representative production benchmark. Report citation coverage, citation accuracy among cited relevant cases, relevant-query abstention, irrelevant false citations, exact-source integrity, and results by language and intent category. Report all denominators and the frozen hashes.
