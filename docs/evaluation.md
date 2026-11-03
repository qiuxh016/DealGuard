# Evaluation

The automated suite verifies deterministic outcomes and persisted side effects:

- P0 v1 ALLOW total, v2 policy change, role/ownership boundaries, self-claimed tier, stock/input errors, expiry, fail-closed workflow and scoped run history.
- P1 review request without quote, manager role enforcement, approval revalidation, rejection/stale behavior, idempotent approval, and trace reason codes.
- Legacy SQLite schema migration preserves policy/run rows and adds approval/draft/metrics schema.
- Policy source upload requires the supported type/encoding/size envelope and remains non-executable until structured admin confirmation.
- Policy Diff compares v1/v2 without quote writes or active-policy mutation.
- Measured duration and CSV values match stored records; token values remain unknown without provider-returned usage.
- Optional inquiry `Idempotency-Key` replay returns the same Run/Quote; actor scopes are isolated, a different inquiry with the same key is rejected, and replay never duplicates approval or quote writes.
- Run migrations preserve the legacy SQLite rows while adding the actor/key uniqueness constraint.
- An expired `running` lease resumes the same Run with a newer attempt number; an active lease remains in progress, and the attempt fence prevents a stale execution from committing.
- Two simultaneous manager approvals resolve to the same approved quote and leave exactly one quote row.
- `data/eval_cases.jsonl` contains 50 frozen deterministic Demo cases: policy threshold matrix for v1/v2/v3, Standard-tier and role boundaries, stock boundaries, missing/invalid fields, absent policy, and expired policy. Each case asserts decision, reason code, policy version/evidence, quote amount, and persisted quote/approval counts.

Run `cd backend; python -m pytest -q` for the full regression suite, or `python -m pytest -q tests/test_evaluation_cases.py` for only the frozen cases. This dataset validates the deterministic Demo parser and Guard contract; it is not a model-quality benchmark. Automated tests explicitly disable provider credentials and local model paths so they remain reproducible. DeepSeek has separate frozen parser evaluations below; their small synthetic sample sizes do not establish generalization, operational error rates, or production reliability.

## Multi-policy retrieval set

`data/policy_retrieval_eval/` contains five simultaneous policy candidates and 50 frozen cases: 35 development cases and 15 holdout cases. The test fixture adds `reference_only`, expired, and cross-tenant decoys, then validates API decisions, citations, and persisted quote/approval counts against the filtered current-policy set.

Run the offline comparison from `backend/`:

```powershell
..\.venv\Scripts\python.exe ..\scripts\evaluate_policy_retrieval.py --split all
..\.venv\Scripts\python.exe ..\scripts\evaluate_policy_retrieval.py --split development
..\.venv\Scripts\python.exe ..\scripts\evaluate_policy_retrieval.py --split holdout
```

The report prints the combined dataset SHA-256, case denominator, Citation Accuracy@1, Recall@3, MRR, and failed case IDs for BM25, a deterministic lexical test embedding adapter, and RRF. Results are explicitly marked `offline_test_adapter_only`; the adapter tests retrieval plumbing and is not a semantic model. On the current fixed set RRF is 98% Top-1 / 100% Recall@3 overall; this is a reproducibility check, not a real model quality claim.

## Local Qwen3 Embedding evaluation

With `LOCAL_EMBEDDING_MODEL_PATH` configured in the repository `.env`, run from `backend/`:

```powershell
$env:LLM_MODEL = ''
..\.venv\Scripts\python.exe ..\scripts\evaluate_policy_retrieval.py --with-local-embedding --split all
```

This runs the frozen 35-case development and 15-case holdout queries through the local Qwen3 adapter and reports model/package versions, the dataset SHA-256, query latency, top-1 errors, and per-split metrics. It also compares BM25+Qwen RRF and an agreement-only selection rule. The rule selects a policy only when BM25 and Qwen choose the same top candidate; disagreement must fail closed. This gate is also tested through the API for both the deny-without-write and agree-and-cite paths.

The 2026-11-03 CPU evaluation is saved in `qwen3-embedding-retrieval-evaluation-2026-11-03.json` and a repeat run in `qwen3-embedding-retrieval-evaluation-2026-11-03-repeat.json`. Both used dataset SHA-256 `eaf82293005b209bc32ea0c8f6eceef0deed375f6d0bef6545c98faefeb6b1e7` and produced identical retrieval metrics:

| Method | Development Top-1 | Holdout Top-1 | Holdout Recall@3 | Notes |
|---|---:|---:|---:|---|
| Qwen3 alone | 65.7% (23/35) | 53.3% (8/15) | 100% | Not safe as a sole policy selector |
| BM25 + Qwen3 RRF | 80.0% (28/35) | 73.3% (11/15) | 100% | Better than Qwen alone, still insufficient |
| BM25/Qwen top-1 agreement | 100% (20/20 accepted) | 100% (8/8 accepted) | — | Coverage is 57.1% development and 53.3% holdout; disagreements abstain |

Mean query embedding time was about 252 ms on CPU, excluding model load and document vectorization. The agreement result is promising but based on only eight accepted holdout cases; it is a conservative local-demo gate, not a production accuracy guarantee. DeepSeek inquiry parsing was not evaluated by these retrieval runs.

## DeepSeek parser smoke check

On 2026-11-03, two synthetic inquiries were parsed with the configured `deepseek-flash` model. The single-item case returned `P-100`, quantity `100`, discount `5%` in 2.19 s (577 input / 100 output tokens). The multi-item case returned `P-100 × 10 at 5%` and `P-200 × 4 at 3%` in 2.50 s (585 input / 121 output tokens). An initial request using LangChain's default `json_schema` mode received HTTP 400 because the DeepSeek Chat Completions endpoint does not accept that response format. The integration now uses function calling and disables thinking for that structured call. These two smoke cases establish connectivity and schema compatibility only; they do not measure parser accuracy, timeout rate, or production reliability.

## Frozen-set DeepSeek parser evaluation

The evaluation script sends each of the 50 synthetic development/holdout queries to the configured chat model twice. It compares normalized product ID, quantity, discount, one-item structure, and preserved customer message against the frozen annotations. It records only parsed fields, token counts, timing, case IDs, and error types; it does not store API credentials, request headers, or raw model responses.

Results are in `deepseek-parser-evaluation-2026-11-03.json` (dataset SHA-256 `eaf82293005b209bc32ea0c8f6eceef0deed375f6d0bef6545c98faefeb6b1e7`):

| Run | Exact cases | Development | Holdout | Mean latency | P50 | P95 | Input / output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 50/50 | 35/35 | 15/15 | 1,011.9 ms | 990.4 ms | 1,410.9 ms | 29,327 / 5,091 |
| 2 | 50/50 | 35/35 | 15/15 | 974.4 ms | 978.9 ms | 1,194.6 ms | 29,327 / 5,077 |

All five measured fields were 100% exact in both runs; there were no failed calls or field mismatches. This is a strong result on this fixed, English, one-item extraction set, but does not evaluate multilingual input, missing/invalid inquiries, or broader customer phrasing. The earlier multi-item smoke case is the only live multi-item check. Automated tests cover malformed structured output and timeout fail-closed behavior without making provider calls.

The provider did not return an invoice amount in usage metadata. At the off-peak rates listed on the [official pricing page](https://api-docs.deepseek.com/quick_start/pricing/), the two runs' combined 58,654 input and 10,168 output tokens imply an approximate **$0.0063–$0.0149** range, depending on input cache hits. This is a rate-based estimate, not a provider-reported charge.

## Expanded language and input-boundary parser cases

`data/intent_parse_eval/cases.jsonl` freezes 20 additional inquiries: 14 development and 6 holdout, with English and Chinese single-item requests, missing fields, zero/negative quantities, invalid discounts and two multi-item requests. Its SHA-256 is `cf4d8c336e84ebd390e876a92bf580f544e08270f1ff6a1d4156d853344f4665`. Run the development set once and holdout twice with the prompt frozen:

```powershell
..\.venv\Scripts\python.exe ..\scripts\evaluate_deepseek_parser.py --dataset edge --split development --runs 1 --output ..\docs\deepseek-parser-edge-development.json
..\.venv\Scripts\python.exe ..\scripts\evaluate_deepseek_parser.py --dataset edge --split holdout --runs 2 --output ..\docs\deepseek-parser-edge-holdout.json
```

After refining the prompt using development cases, development reached 14/14 exact with no failed calls. On the holdout, both runs reached 5/6 exact with no failed calls; each run missed a different Chinese incomplete/invalid single-item inquiry. The parser's fail-safe sanitizer discarded those malformed item structures and returned missing fields, so no invalid number could proceed to Guard, but useful product/quantity fields were lost. The holdout was not used to change the prompt.

To address loss of valid fields on a malformed **single** item without weakening multi-item atomicity, the sanitizer now recovers safe product/quantity/discount fields into the legacy single-item shape and replaces only invalid fields with `null`; malformed multi-item requests still clear the entire list. A new independent set, `data/intent_parse_eval/cases_v2.jsonl`, was frozen before this code change (SHA-256 `7e94a8dbfc3326929e138039e2542f3818d1c0aff9faed28678062aaa30116dd`): 12 development and 6 holdout cases, including Chinese missing/invalid fields, single-item invalid output, and valid/invalid multi-item requests. The prompt was unchanged during this v2 evaluation.

Run it with:

```powershell
..\.venv\Scripts\python.exe ..\scripts\evaluate_deepseek_parser.py --dataset edge_v2 --split development --runs 1 --output ..\docs\deepseek-parser-edge-v2-development.json
..\.venv\Scripts\python.exe ..\scripts\evaluate_deepseek_parser.py --dataset edge_v2 --split holdout --runs 2 --output ..\docs\deepseek-parser-edge-v2-holdout.json
```

Development scored 12/12 exact (8 English, 4 Chinese). The untouched holdout scored 6/6 in both runs (2 English, 4 Chinese); there were no failed calls. Development mean latency was 1,115 ms. Holdout mean latency was 1,167 ms and 1,151 ms. This is encouraging on a small synthetic set and not a general language-quality guarantee. The earlier v1 holdout remains unchanged and was not reused for tuning.

Focused workflow tests passed 43/43 after adding a regression asserting that a malformed single-item discount preserves its valid product and quantity while producing no quote. Full SQLite backend regression passed 221 tests; provider credentials and local embedding path were explicitly disabled for deterministic tests. PostgreSQL has not been rerun after this parser-only change.

The original 50-case English retrieval-query set was rerun twice after the prompt change: both runs remained 50/50 exact, with no failed calls. The repeat report is `deepseek-parser-retrieval-after-edge-prompt.json`. Across the expanded-set calls and that regression rerun, 90,339 input and 14,697 output tokens were reported. Applying the off-peak rates on the [official pricing page](https://api-docs.deepseek.com/quick_start/pricing/) gives an approximate **$0.0091–$0.0224** range depending on input cache hits; this is not an invoice amount.

The manager/Admin `/evaluation` page reads the report through `GET /api/v1/evaluation`, supports split selection, and downloads the JSON report. Run history and policy management audit are available from `/runs` and `/audit`; their APIs scope records to the authenticated tenant and role.

## Expanded multilingual policy retrieval set

`data/policy_retrieval_eval/cases_v2.jsonl` adds 20 new policy-topic queries, split evenly between development and holdout and balanced across five policy topics and English/Chinese. All cases use the same five eligible candidate IDs and separately frozen expected policy labels. The file SHA-256 is `066629843330e2c26fbf4a29a6d186e43ee6913e49b70ba3cd38a109e255db09`; the report hash also includes the unchanged policy source file and is `8fdfa134c2a3d0fd5c52532980112e074e6c1906a569f05ce3d850ec322aaa06`.

Run the local Qwen3 comparison twice from the repository root (adjust the model path to the local full-weight directory):

```powershell
$env:LOCAL_EMBEDDING_MODEL_PATH = 'D:\gitclone\模型\Qwen3-Embedding-0.6B'
.\backend\.venv\Scripts\python.exe scripts\evaluate_policy_retrieval.py --dataset retrieval_v2 --with-local-embedding | Set-Content -Encoding utf8 docs\policy-retrieval-v2-evaluation.json
.\backend\.venv\Scripts\python.exe scripts\evaluate_policy_retrieval.py --dataset retrieval_v2 --with-local-embedding | Set-Content -Encoding utf8 docs\policy-retrieval-v2-evaluation-repeat.json
```

Both runs produced identical retrieval metrics:

| Method | Development Top-1 | Holdout Top-1 | Overall Recall@3 | Notes |
|---|---:|---:|---:|---|
| Qwen3 alone | 10/10 | 10/10 | 20/20 | Mean query embedding 220 ms; model load about 8.9 s |
| BM25 | 7/10 | 7/10 | 17/20 | Misses are Chinese loyalty, early-payment, and product-category paraphrases |
| BM25 + Qwen3 RRF | 7/10 | 7/10 | 20/20 | RRF does not fix those six Top-1 misses |
| BM25/Qwen agreement gate | 7/10 accepted, 7/7 correct | 7/10 accepted, 7/7 correct | — | 70% coverage per split; the other 3/10 abstain |

These are controlled synthetic retrieval-ranking results on 10 holdout cases, not broad semantic quality or production authorization results. The API/Guard replay was also run twice over the 20 cases with the local Qwen3/BM25 selector and isolated SQLite. A separate four-row Guard expectation file corrects the review reason code to the actual stable API code `MANAGER_APPROVAL_REQUIRED` without changing the frozen retrieval dataset. Run with:

```powershell
.\backend\.venv\Scripts\python.exe scripts\replay_policy_guard_eval.py --dataset retrieval_v2 --model-path 'D:\gitclone\模型\Qwen3-Embedding-0.6B' --output docs\policy-guard-v2-replay.json
.\backend\.venv\Scripts\python.exe scripts\replay_policy_guard_eval.py --dataset retrieval_v2 --model-path 'D:\gitclone\模型\Qwen3-Embedding-0.6B' --output docs\policy-guard-v2-replay-repeat.json
```

Both API replay runs were identical after making local cosine ranking and BM25 accumulation deterministic:

| Metric | Development | Holdout | Total |
|---|---:|---:|---:|
| Correct policy citation | 6/10 | 5/10 | 11/20 |
| Decision matches expected | 8/10 | 6/10 | 14/20 |
| Reason code matches | 8/10 | 5/10 | 13/20 |
| Full task success (citation, decision, code, writes) | 6/10 | 5/10 | 11/20 |
| Wrong-policy ALLOW cases | 2 | 0 | 2 |
| False rejections | 2 | 4 | 6 |
| Unexpected quote/approval writes | 0 | 0 | 0 |

The wrong-policy ALLOW cases are Chinese loyalty/product inquiries where retrieval cited the bulk policy; this is a real applicability gap even though the selected rule happened to produce the expected ALLOW outcome. Several Chinese cases safely abstained, creating false rejections. Thus, two-retriever agreement is not sufficient proof that a policy applies. This motivated the structured applicability scope and independent Guard check documented in the scoped replay section below. Reports: `policy-guard-v2-replay.json` and `policy-guard-v2-replay-repeat.json`; retrieval corpus hash remains `8fdfa134c2a3d0fd5c52532980112e074e6c1906a569f05ce3d850ec322aaa06`, Guard expectation hash is recorded in each report.


## Policy RAG citation and safety evaluation

### Frozen intent challenge baseline

Reproduce the frozen lexical challenge with `.\backend\.venv\Scripts\python.exe scripts\evaluate_policy_rag_challenge.py` from the repository root. The dataset SHA-256 is `41e1e233abb56828ff4498c750f8901c36fb38faf5a85e12aab560ec9abeb00d`; the frozen scorer SHA-256 is `4dba168ef79c9f314b60420e7f90970a4d0761e833c0fb6c403eb71bdc0aea37`. The 14-case set reproduces 60% relevant citation coverage, 83.3% citation accuracy among cited relevant queries, 40% relevant abstention, 100% false citations on four near-topic non-policy queries, and 100% source-substring integrity. By intent, explicit mixed-language policy requests cite 5/5; related paraphrases cite 1/5 and abstain 4/5; near-topic non-policy requests cite 4/4.

The detailed case IDs and intent split are in `policy-rag-challenge-intent-analysis-2026-11-03.json`. This set was written by the implementation author, so it is not an independent or blind evaluation. Do not tune the scorer from it. The next evaluation requires an independent author; the handoff rules are in `independent-policy-retrieval-challenge-author-brief.md`.

`scripts/evaluate_policy_rag_safety.py` evaluates the existing 20-case bilingual retrieval_v2 set plus six frozen safety boundaries. The harness injects expired, cross-tenant, wrong-scope and reference-only decoys for each case, includes one overlapping-rule conflict, and adds two same-scope policies with identical structured authorization rules to exercise multi-candidate selection. For those two cases it uses the same product/quantity/discount prefix as the API replay. It reports candidate-filter precision, decoy leaks, conflict precision/recall, decision coverage, selective policy accuracy, correct-abstention recall, exact clause Citation Accuracy@1, source-substring integrity, language breakdown and offline latency. It does not create quotes or call the inquiry parser.

The corpus in `data/policy_rag_safety/policies.json` uses versioned Markdown with headings, eligibility clauses, direct limits, approval exceptions and commercial-effect clauses. It is a fixed evaluation fixture, not customer-uploaded or live HarborLight policy text. Offline Qwen timing excludes model load and provider/runtime overhead. Reports are `policy-rag-safety-bm25-baseline.json` and `policy-rag-safety-qwen3.json`; combined dataset SHA-256 is `4f9a2964d3b10d2e4df8d8d4db17870eeb3d291b924c4dfb8118684171968a18`.

One CPU run using the local `Qwen3-Embedding-0.6B` checkpoint (`sentence-transformers 5.7.0`, `transformers 4.57.6`, `torch 2.15.0`) produced:

| Metric | BM25 | Qwen3 | Notes |
|---|---:|---:|---|
| Exact clause Citation Accuracy@1 | 76.2% (16/21) | 95.2% (20/21) | BM25 English 100%, Chinese 50%; Qwen3 English 90.9%, Chinese 100% |
| Citation is exact source substring | 100% | 100% | Integrity does not imply relevance |
| Candidate filter precision | 100% | 100% | 0 unexpected candidates in the offline harness |
| Expired / cross-tenant / wrong-scope / reference-only leaks | 0 | 0 | Each category had decoys |
| Conflict precision / recall | 100% / 100% | 100% / 100% | One positive overlap case |
| Decision coverage / correct abstention recall | 76.9% / 100% | 80.8% / 100% | Qwen and BM25 agree on the consensus case; disagreement safely abstains |

The Qwen3 citation miss is `safety-v2-product-en-holdout`; BM25 failures are listed in its JSON report. This is a small 10-case holdout, so it does not establish general policy-document quality. Reproduce the lexical harness with `.\.venv\Scripts\python.exe ..\scripts\evaluate_policy_rag_safety.py --output ..\docs\policy-rag-safety-bm25-baseline.json` from `backend/`; add `--with-local-embedding` and set `LOCAL_EMBEDDING_MODEL_PATH` to compare the local model.

The same Markdown corpus was replayed through FastAPI TestClient, isolated SQLite, the actual workflow/Registry/Guard, approval persistence, quote persistence and CSV/API schemas. Results are in `policy-rag-safety-api-qwen3.json` (23 cases, report dataset hash `3ec612790af302d2e4df8d8d4db17870eeb5e540856845dd280b5aa42630fd6e`): development and holdout each achieved 10/10 on policy citation, exact stored clause, versioned line reference, decision, reason code, persistence outcome and per-line provenance hash/citation binding. The safety cases include one conflict, one BM25/Qwen consensus and one disagreement. Retrieval modes were `registry` 20, `conflict_graph_abstain` 1, `local_consensus` 1 and `local_abstain` 1. All 23 decisions, reason codes and write outcomes matched; there were no wrong-policy ALLOWs, false rejections, or unexpected quote/approval writes. The disagreement returned `POLICY_RETRIEVAL_AMBIGUOUS` and wrote no quote; the consensus case cited the selected policy's stored clause and created one draft.

Replay command from the repository root:

```powershell
$env:LOCAL_EMBEDDING_MODEL_PATH = 'D:\gitclone\模型\Qwen3-Embedding-0.6B'
.\backend\.venv\Scripts\python.exe scripts\replay_policy_guard_eval.py --dataset rag_safety --model-path $env:LOCAL_EMBEDDING_MODEL_PATH --output docs\policy-rag-safety-api-qwen3.json
```

## 结构化政策范围后的 API/Guard 回放

Registry 现在将候选政策限制到服务端从原始询盘推断出的适用范围；通用政策作为兼容回退，商品专属政策还要求商品 ID 一致。LLM 无法自行赋予授权范围；询盘同时命中多个范围时工作流要求澄清。Guard、审批重验和报价修改均独立检查已保存范围。

在相同 retrieval_v2 的 20 条用例上使用本机 `D:\gitclone\模型\Qwen3-Embedding-0.6B` 重放，结果记录在 `policy-guard-v2-scoped-replay.json`：

| 指标 | Development | Holdout | Total |
|---|---:|---:|---:|
| Citation / decision / reason accuracy | 10/10 | 10/10 | 20/20 |
| Full task success | 10/10 | 10/10 | 20/20 |
| Wrong-policy ALLOW | 0 | 0 | 0 |
| False rejection | 0 | 0 | 0 |
| Unexpected quote/approval writes | 0 | 0 | 0 |

这组样本规模仍小且为合成数据，不能代表真实自然流量。政策范围、Registry 激活和 Guard 回归为 66 项通过；前端 ESLint、TypeScript 和 production build 通过。
# Agent matrix and policy impact replay (2026-11-03)

Run the fixed A/B/C/D matrix from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe scripts\evaluate_agent_matrix.py --output docs\agent-evaluation-matrix.json
```

The matrix uses the frozen 50-case `data/eval_cases.jsonl` set and isolated in-memory SQLite databases. A is fixed workflow + BM25 + Guard; B adds bounded Tool Calling; C adds local Qwen3 hybrid retrieval; D combines both. The deterministic provider adapter removes network/model variance. One separate tool-capability case checks that B/D resolve an inquiry-only product name with exactly one read-only catalog search while A/C request the missing product ID. The report labels the adapter mode and dataset SHA-256; it is an engineering regression/ablation, not a live-provider quality estimate. A Guard-disabled variant is intentionally absent because it would not be useful to permit writes.

The 2026-11-03 report is `agent-evaluation-matrix-2026-11-03.json`: all four variants passed 50/50 frozen business cases, with zero unauthorized writes. The dedicated product-name challenge passed for B and D and selected `search_products`; A and C correctly stayed at `NEEDS_INPUT`. Local Qwen3 warm-path latency is higher than BM25-only, and the first C request includes model load. The challenge and business regression sets are synthetic and do not establish production traffic performance.

For a bounded real-provider check, run only the small identity-boundary split with the credentials already configured in `.env`:

```powershell
.\backend\.venv\Scripts\python.exe scripts\evaluate_agent_matrix.py --split identity_boundary --live-llm --output docs\agent-evaluation-live-identity.json
```

The saved 2026-11-03 DeepSeek run (`agent-evaluation-live-identity-2026-11-03.json`) passed 5/5 core identity-boundary cases in A/B/C/D. B and D resolved the additional product-name case using `search_products`; A/C safely denied the unresolved catalog name, with no quote/approval write. Unauthorized writes were zero in every variant. Input/output token totals were A 4,662/725, B 10,949/4,838, C 4,662/737, D 13,931/4,230. Measured P95 latency was A 1.38s, B 9.99s, C 18.52s, D 9.43s; the sample has only five cases per variant and includes local Qwen CPU latency, so these figures are diagnostic rather than SLA or broad model-quality claims. Pricing was not inferred from token counts.

Policy impact is available to workspace administrators at `POST /api/v1/policy-impact/simulate`. Submit a structured inactive `policy_draft_id`, up to 50 optional `run_ids`, and either `frozen_intent_replay` or `end_to_end_replay`. Frozen mode skips historical runs that predate trusted snapshot persistence. End-to-end replay includes parser/planner/retrieval/Guard and rolls back its database savepoint. The response explicitly returns `writes_created: false`. Tests cover an ALLOW-to-DENY policy change and verify quote/approval counts remain unchanged.
