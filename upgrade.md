# Engine AI: source review and upgrade plan

Status: Proposed
Reviewed: 27 October 2026
Repository: https://github.com/MoblyJ/engine-ai
Observed main revision: 421de2f78bf56e3def370c364f8136ee7a0827f3 (v0.15.0)

## Recommendation

Keep the existing Python toolkit and npm distribution. Evolve it into a provider-independent coding runtime with bounded context, independent model profiles, durable task scheduling, isolated workspaces, and executable verification. Preserve Claude Code compatibility. More agent Markdown files alone will not deliver this.

Today Claude Code owns the model/tool execution loop. Engine AI supplies MCP tools, agent instructions, memory, retrieval, scaffolding, and app sessions. Independent OpenRouter agents require a runtime that owns their model requests, tool dispatch, state, permissions, and budgets.

Scope: browser-based review of README, package.json, install.sh, bin/engine-ai.js, orchestrator/builder agent definitions, mcp/engine.py, memory.py, knowledge.py, experts.py, sessions.py, forge_mcp.py, deploy.py, integrations.py, and tests/test_mcp.py; agent and test directory listings also inspected. I did not run the repository test suite because source transfer through the execution environment failed. This is a source review, not a complete security audit. No repository changes or API calls with user credentials were made.

## 1. Concrete changes to existing files

| Priority | File | Observation | Recommended change |
| --- | --- | --- | --- |
| P0 | mcp/memory.py | Pockets have no project/session namespace. Save scans all pockets and merges by keyword overlap; data merges last-write-wins. | Add project/repository/task identity, provenance, validity, and revision. Never merge project facts merely because keywords overlap. |
| P0 | mcp/deploy.py | Readiness checks file existence; has_start_script only checks package.json exists; test-like filenames count as tests. | Separate inventory from actual build/test evidence. Parse scripts and execute checks; bind results to an exact commit. |
| P0 | mcp/deploy.py | Scaffold writes existing manifests, server files, ignore files, and workflows using write mode. | Preview changes; refuse overwrite by default; require explicit replace behavior and preserve originals. |
| P0 | mcp/forge_mcp.py | Exceptions produce isError:true, but handler results with ok:false/error are serialized normally. Invalid JSON is discarded; initialization echoes requested protocol. | Typed tool outcomes, correct operational/protocol error distinction, request/schema validation, supported-version negotiation, malformed-input tests. Consider official MCP SDK. |
| P0 | mcp/engine.py | Vault uses a custom SHA-derived keystream/XOR/HMAC construction labeled dev-grade. | Migrate to OS credential storage or maintained authenticated encryption. Keep plaintext out of prompts and logs. |
| P0 | mcp/integrations.py | git_publish checks for a .git directory, stages all changes, ignores several Git exit codes, and always attempts repo creation. Worktrees have .git files and share repository configuration. | Git-aware worktree detection, checked subprocess results, explicit staging, idempotent remote/PR handling, and tests for remote isolation across apps. |
| P1 | agents/engine-orchestrator.md | Mandatory sequential full-context assembly, expert consultation, and accumulated handoffs. | Dependency graph, small task packets, conditional experts/research, and evidence-based escalation. |
| P1 | mcp/engine.py | Embeddings are 512-dimensional hashed word counts. Index is deleted/rebuilt; search scans all vectors and returns short prefixes without chunk locations. | Incremental hashes, exact/FTS retrieval, symbol spans and dependencies; optional learned embeddings only after evaluation. |
| P1 | mcp/knowledge.py | context_pack concatenates memory/snippets without a token budget. Existing source clones are ingested without an update/fetch step. | Budgeted context compilation; source commit/version/freshness; explicit atomic refresh. |
| P1 | mcp/memory.py | Merged text retains only the final 8,000 characters; recall sends complete selected pockets. | Separate compact decisions from immutable evidence; resolve contradictions and retrieve details on demand. |
| P1 | mcp/sessions.py | Isolation is per app from a central repo, not per task in a team's existing repo. Fallback initialization can record a different branch name from the branch created. Path matching assumes slash separators. | Project/run/task worktrees, validated actual branch state, path-aware containment, explicit isolation failures. |
| P1 | mcp/experts.py | Domain routing uses substring triggers and is not model capability/quality routing. | Phrase/token matching, limited expert selection, typed task profiles, evaluated model assignments. |
| P1 | mcp/integrations.py | Mobile audit uses regex heuristics. Vercel functions do not themselves retrieve tokens from the vault. | Real browser verification plus static hints; one explicit integration secret-resolution path. |
| P1 | package.json / bin/engine-ai.js / install.sh | Linux/macOS-only package; Bash/Claude dependency; some spawn failures may return success via status-or-zero. Invalid settings JSON can become empty config; broad hook matching removes whole groups. | Standalone mode; correct exit codes; platform adapters; atomic, ownership-aware settings changes; preserve malformed settings. |
| P2 | README | Tool/test counts drift; unscoped npx invocation conflicts with the documented scoped package. | Generate counts, fix package examples, document only tested platform/behavior guarantees. |

check_same_thread=False on SQLite connections is not a multi-worker concurrency protocol. Add transactional state ownership/leases before introducing parallel execution.

## 2. Architecture decision

Keep Python for the runtime and Node as the npm/CLI launcher. Gradually extract reusable code into engine_core; keep existing MCP names through compatibility wrappers. Do not rewrite everything into another language just to support npm.

Core components:

1. Project service: repository identity, requirements, contracts, configuration.
2. Planner: validated task dependency graph and acceptance criteria.
3. Context compiler: bounded relevant code, decisions, contracts, and failure evidence.
4. Provider gateway: model catalog, credentials, capabilities, quotas, retries, usage/cost.
5. Worker loop: request -> validated tool calls -> bounded results -> continuation -> outcome.
6. Scheduler: leases, retries, deadlines, cancellation, resource budgets, checkpoints.
7. Workspace manager: task worktrees and one serialized integrator.
8. Verifier: builds, tests, contract/browser checks against exact revisions.
9. Event/artifact store: decisions, logs, patches, evidence, and usage.

Use local SQLite with migrations, short transactions, appropriate WAL settings, and leases. Store large artifacts separately by content hash. Do not share a SQLite file over a network filesystem among four laptops. Use GitHub issues/PRs for the first team workflow; add an authenticated coordination service only when distributed task leasing is needed.

Proposed new modules:

- engine_core/providers/{base,openrouter,local,catalog,limiter}.py
- engine_core/runtime/{worker,scheduler,task_graph,state}.py
- engine_core/context/{compiler,indexer,symbols,retrieval}.py
- engine_core/memory/{store,migrations,decisions}.py
- engine_core/workspaces/{git,integration}.py
- engine_core/tools/{registry,executor,policy}.py
- engine_core/verification/{runner,evidence}.py
- engine_core/telemetry/{usage,events}.py
- tests/evals/, tests/runtime/, tests/providers/, tests/workspaces/

These paths are proposals, not existing files.

## 3. Context efficiency without sacrificing quality

Give each worker a task packet containing objective, acceptance criteria, permitted paths, base SHA, API/schema contracts, relevant source spans, verified decisions, and the latest relevant failure. Do not forward every prior agent transcript.

Workers return structured outcomes: status, patch/commit reference, changed files, checks and evidence IDs, unresolved questions, and a compact handoff. Persist full logs outside model context.

Start with an adjustable 8k-16k input-token target for routine tasks, not an inflexible cap on all work. Reserve capacity for tool schemas, output/reasoning tokens, and margin within the model's actual context window. Expand when missing evidence justifies it. Illustrative 8k allocation: 1k instructions/tools, 1k objective/contracts, 4k code, 1k decisions, 1k errors. Tune by measured task outcomes.

Retrieval sequence: exact paths/symbols -> FTS/BM25 -> dependency neighbors -> optional semantic reranking. Fetch full relevant functions/interfaces with file hash, lines, and revision. Incrementally reindex changed/deleted files; respect ignore rules and secret exclusions. Existing fixed prefixes are too shallow for reliable edits.

Cache file summaries/index results by project, file hash, parser version, and model/prompt version. Cache test results only with complete-enough code/dependency/environment keys; stale cached success must never authorize merging. Do not reuse a code patch from a semantic-response cache across repository revisions.

Memory needs separate categories: verified facts, decisions, proposed ideas, temporary errors, and superseded records. Project facts are scoped; reusable general knowledge is opt-in. A model claim is not verified evidence. Store immutable source references so compaction can be checked against originals.

Research should be conditional on unknown or time-sensitive dependencies. Do not summon three experts or require a class diagram for every small change. Choose API contracts, state diagrams, data models, or short ADRs when those fit the problem.

Run formatting, linting, schema validation, compilation, and test discovery in code. Use models for design ambiguity and diagnosis.

Provider prompt caching can lower repeated-prefix cost/latency where supported. It does not reduce context-window occupancy. Keep stable instructions/tool schemas first, dynamic task state later, and verify actual cache hits. Do not assume every free route supports caching.

## 4. OpenRouter: ten profiles, not quota rotation

OpenRouter's terms prohibit multiple accounts created by a single user to circumvent limits. Its limits page also states that extra accounts or API keys do not increase rate limits. Do not make ten-account rotation the scaling design.

Ten logical profiles are useful: planner, frontend, backend, services, database, test-author, reviewer, security-review, researcher, documentation. Each can reference its own key/model, or reuse a key. Keys provide attribution, revocation, and spending separation; they do not guarantee separate account quotas.

For genuine multiple users, preserve credential ownership and an appropriate team/account arrangement. Do not treat unrelated users' credentials as a shared quota pool.

Published free limits at review time: 20 requests/minute, 50/day, rising to 1,000/day after at least $10 in purchased credits. Failed attempts count toward the daily cap. Discover current account limits and handle headers; do not freeze those numbers in code. Ten workers making six model turns each need 60 requests before retries, exceeding a 50/day allowance. Tool round trips also consume model calls.

Start with two active coding workers and one heavy test/build process; measure before increasing to 3-4 inference workers. Ten roles do not require ten simultaneous processes. This is a conservative starting point for the previously discussed 16 GB laptop, not a measured capacity result.

Gateway requirements:

- Load the supported model catalog; validate prices, context, output capacity, tool support, and structured-output support. Check endpoint-specific capability where needed.
- Pin explicit evaluated model IDs for coding. Do not infer coding ability from popularity or context length.
- openrouter/free selects among eligible free models and is useful for experiments/low-risk work. Record the selected model; do not assume stable model behavior across requests.
- Keep a task on one model where possible. On fallback, validate compatibility and pass a compact state checkpoint.
- Rate-limit by account group and relevant endpoint; reserve slots atomically across workers. Honor Retry-After and use bounded jittered retries.
- Pause/checkpoint on quota exhaustion. Authentication/configuration failures should not trigger endless retries or account rotation.
- Default to free-only and no paid fallback. Paid escalation requires a configured allowlist and budget. Revalidate prices and stop if no acceptable free route exists.
- Bound turns, calls, output, elapsed time, cost, and repeated failures; reserve quota for review/integration.
- Journal tool effects; do not repeat writes blindly after transport failures.
- Resolve credential references in the host, never in model prompts, shared config, or telemetry.

Illustrative proposed config (not supported by v0.15.0):

~~~yaml
version: 1
execution:
  max_active_workers: 2
  max_build_processes: 1
  max_model_turns_per_task: 8
routing:
  free_only: true
  paid_fallback: false
  quota_exhausted: pause
profiles:
  frontend:
    provider: openrouter
    credential_ref: env:OPENROUTER_FRONTEND_KEY
    account_group: team-primary
    model_ref: catalog:validated-frontend-model
    input_target_tokens: 12000
    max_output_tokens: 4000
    requires: [tools]
  backend:
    provider: openrouter
    credential_ref: env:OPENROUTER_BACKEND_KEY
    account_group: team-primary
    model_ref: catalog:validated-backend-model
    input_target_tokens: 16000
    max_output_tokens: 4000
    requires: [tools]
  reviewer:
    provider: openrouter
    credential_ref: env:OPENROUTER_REVIEW_KEY
    account_group: team-primary
    model_ref: catalog:validated-review-model
    read_only: true
~~~

Catalog references are placeholders to resolve into real validated IDs during setup. These three keys share one account budget. A profile's read_only flag must be enforced by the executor, not merely included in its prompt.

A local provider adapter can later use the RTX 3060 for evaluated small-model tasks or embeddings. Check actual VRAM and memory usage first; do not assume a particular local model fits or outperforms cloud alternatives.

## 5. Reliable parallel development

First define user journeys, acceptance criteria, API contracts, data/migration rules, UI states, and authorization behavior. Build one thin end-to-end feature, then parallelize independently owned tasks.

TaskSpec fields: task ID, project ID, dependencies, base SHA, owned paths, shared contract version, acceptance criteria, verification commands, profile, budgets. Persist pending/ready/leased/running/verifying/blocked/failed/complete states. Leases need expiration and fencing so a late worker cannot overwrite a newer attempt.

Frontend, backend, and services can run in parallel after their interfaces agree. QA can author tests from requirements meanwhile; final integration tests must run on the combined revision.

Use task branches/worktrees within the real project repository. Serialize changes to shared contracts, migrations, lockfiles, and root manifests. Worktrees are not OS security boundaries: use constrained execution for generated code, deny access to credential stores, strip inherited secrets, and avoid exposing the Docker socket.

Only one integrator updates the integration branch. Recheck base SHA and required tests after earlier merges. Branch-local success is not proof of combined compatibility. Conflicts require explicit resolution tasks, not automatic ours/theirs. GitHub protections/reviews remain enforced.

## 6. Sophisticated products require evidence

Add stack-specific capabilities when needed: authentication/authorization, persistence/migrations, validation, external-service failure recovery, accessible loading/empty/error states, observability, and deployment-specific checks. Avoid forcing Docker or microservices onto every product.

Replace readiness 100 with an evidence manifest: tested SHA, environment, dependency versions, commands, exit codes, logs/artifacts, and requirements covered. Relevant gates include mobile/desktop browser journeys, API contract tests, negative permission tests, migration upgrades, and cross-component end-to-end flows.

Build a fixed 15-20-task benchmark with representative bug fixes, frontend/backend integration, schema/auth changes, refactoring, unreliable external APIs, and interrupted runs. Protect independent acceptance tests from implementation workers.

Measure total input/output/reasoning/cached tokens when available, calls, retries, latency, cost, pass rate, regressions, and human corrections. Compare the same model with/without context compilation before changing models and parallelism.

Proposed target: at least 30% lower median input tokens per accepted task without reducing held-out pass rate or adding critical regressions. This is a target, not measured savings. Optimize cost per accepted feature; a more capable model may cost less overall if it avoids repeated failures.

## 7. Staged delivery

| Phase | Deliverables | Exit criterion |
| --- | --- | --- |
| 0: Correctness | Regression fixtures; scoped memory; safe scaffolding; tool outcomes; Git/worktree fixes; truthful readiness | Existing tests plus new failure-path tests pass |
| 1: Context | Incremental index, source spans, migration, budgeted packets, optional experts | No cross-project recall; measured budgets and benchmark comparison |
| 2: Standalone worker | OpenRouter adapter, catalog, keys/limits, tool executor, checkpoint/resume | One task completes without Claude; 429/auth/invalid-output/crash paths covered |
| 3: Parallel execution | Task DAG, leases, worktrees, file ownership, integrator | Two tasks integrate; stale base/conflicts/duplicate effects cannot silently corrupt state |
| 4: Team workflow | Existing-repo join, role mapping, PR/check status, safe sync | Four clones collaborate without shared filesystem assumptions |
| 5: Expansion | Evaluated profiles, browser gates, selected templates, dashboard over events | Representative product works and quality/cost improvement is demonstrated |

Four-person ownership: A handles providers/credentials/limits; B handles context/index/memory; C handles scheduler/worktrees/CLI; D handles verification/evaluation/end-to-end tests. Agree TaskSpec, ContextPacket, ToolResult, and EvidenceManifest contracts first. Assign one owner to shared manifests.

First hackathon demo: one repo, one feature, two coding workers, one verifier, explicit free model choices, resumable runs, and a PR with evidence. Defer ten simultaneous agents, a distributed control plane, and a polished dashboard.

Proposed future CLI: engine-ai init; providers add openrouter; models refresh; models evaluate; plan; run --max-workers 2 --free-only; status; usage; resume RUN_ID; verify --commit SHA; submit. Preserve current connect/doctor/knowledge commands. Credentials should be entered locally through a hidden prompt or environment.


