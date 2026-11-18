# MTM Planner

Agent system for the **Mind to Machine Hackathon** (GDG on Campus, COMSATS University Wah).
It reads a pack of messages (English / Roman Urdu emails, WhatsApp texts, notices) and plans a 4-hour
physical hackathon: **venue, schedule, food, judging, budget**, with every figure linked to the message it
came from, six validity checks, a full model-call trace and a web UI.

**Design in one line:** the model only *reads*. One batched call turns messages into small JSON claims, each with a
verbatim evidence span. Plain code then checks the evidence, resolves conflicts, plans, and validates. Fewer model
calls, fewer tokens, and nothing the model says reaches the plan unchecked.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env                    # then fill it in (see "Model setup")
python -m engine.llm --selftest         # prints BONUS-SAFE: yes/no  (run this first)
python main.py messages.txt             # CLI: full run or update (see below)
python server.py                        # UI at http://127.0.0.1:8000
python -m pytest tests -q               # offline tests, no model or network needed
```

**One command, two modes** (decided by code, not by you):
- no saved state, or the file holds more than 3 messages: **fresh full run** (state is reset);
- 3 messages or fewer with saved state: **update**. Only messages whose hash is not in the ledger are processed;
  re-running the same message is a no-op.

Outputs go to `output/` (`plan.json`, `trace.txt`, `final_plan_and_trace.txt`). `state/` holds the ledger, runs and
model cache. Both are git-ignored along with `.env`.

The UI calls the same `orchestrator.process()` as the CLI. Tabs: Plan, Checks, Budget, Schedule, Trace, Message Ledger,
Prediction Slip, Export, Run full pack, plus an always-visible Update panel with Plan Diff.

## Model setup

1. Get a key at <https://aistudio.google.com/apikey>.
2. `cp .env.example .env`, set `GEMINI_API_KEY`.
3. Put the organizers' **confirmed** small-model names in `.env` (`MODEL_PRIMARY`, `MODEL_FALLBACK`,
   `SMALL_MODEL_INCLUDE` / `SMALL_MODEL_EXACT`). The list was not in the task PDF; see "Open questions".
4. `python -m engine.llm --selftest` and check it prints `BONUS-SAFE: yes`.

**Local alternative (bonus-safe without any list):** any model served on this laptop counts. Install Ollama, pull a
small model, then in `.env` set `LLM_PROVIDER=openai_compatible`, `OPENAI_BASE_URL=http://localhost:11434/v1`.
Localhost models are treated as small automatically, but `SMALL_MODEL_EXCLUDE` still wins. If outputs look truncated,
raise Ollama's context length (I believe the setting is `OLLAMA_CONTEXT_LENGTH`; please verify in the Ollama docs).

How models are chosen (`engine/llm.py`): discover what the provider offers, filter to small models (EXCLUDE always
beats INCLUDE; embedding/rerank/audio names are dropped), order as `MODEL_PRIMARY`, `MODEL_FALLBACK`, then the rest of
the discovered small models. Each call has a 25 s timeout; a timed-out model is abandoned and the next one used. If
discovery fails, the names in `.env` are used. The chain is cached 10 minutes in `state/models.json`. A non-small model
can never be called (a guard raises before any request). With no small model the run stops with a clear error.

## Modules

| Module | What it is for | What breaks without it |
|---|---|---|
| `main.py` | CLI entry: runs the orchestrator, prints trace and plan | no command-line run |
| `server.py` | Web UI + JSON API (same orchestrator) | no UI, no prediction slips |
| `engine/splitter.py` | Splits any message file (JSON, CSV/TSV, numbered, blocks, headers, WhatsApp lines, one blob) and hashes messages | file format surprises; update detection |
| `engine/reader.py` | The one batched "reading" model call and the grounding check (evidence must be in the message, numbers readable from it) | no claims; or hallucinated figures get through |
| `engine/lang.py` | Number/clock/date reading: digits, English and Roman-Urdu words, lakh, k, hazaar, hours | grounding fails on "5 lakh", "teen ghantay" |
| `engine/areas.py` | Registry of 17 constraint kinds, area dependencies, translation call for unknown kinds | new kinds of rule are ignored |
| `engine/resolver.py` | Sender trust tiers, conflict rules, stale/impersonation/unit/plausibility gates, pending requests | wrong messages change the plan |
| `engine/planner.py` | Pure-code planning of judging, venue, food, schedule, budget, incremental per area | no plan |
| `engine/validator.py` | Re-computes the six scored checks independently of the planner | invalid plans are not caught |
| `engine/orchestrator.py` | Full run / update flow, culprit isolation, commit-or-reject | no state handling |
| `engine/llm.py` | Model discovery, small-model filter, timeouts, fallback, selftest | no bonus safety |
| `engine/trace.py` | Numbered call cards, `[CODE]` lines, `[REJECTED]` lines, token totals | no auditable trace |
| `engine/store.py` | JSON state, atomic writes, text/JSON outputs | no persistence |
| `ui/index.html` | Single static page (vanilla JS/CSS, no CDN, no external fonts) | no UI |
| `ui/branding.json` | Organizer and sponsor strip; never read by the engine | placeholder branding only |

## Decisions: trace honesty

- A numbered `CALL n` exists only when a provider answered. Token counts are exactly what the provider reported;
  if it reported none the trace says `not reported` and the footer counts those calls separately (never estimated).
- Failed attempts with no usage (timeouts, HTTP errors) appear as `[CODE]` lines only, never as numbered calls.
  Output that arrived but was unusable *is* a numbered call, because it consumed tokens.
- The model name shown is the one the provider reported; if absent, the requested name is shown marked `(requested)`.
- Every `[CODE]` step shows `0 tokens`. Each call carries exactly one area tag (reading, venue, schedule, food,
  judging, budget) and a `small-model: yes/no` field. Rejections print `[REJECTED] msg N: <reason with numbers>`.
- Limits are enforced in code: full run < 120 s and 8 calls; update < 60 s and at most 5 calls.

## Decisions: saving tokens

- One batched reading call for the whole pack (about 2,400 characters of system prompt), not one per message.
- The model returns compact JSON; area, unit and most `kind` fields are derived in code.
- Everything that is arithmetic or rules is code: teams, judges, venue, food tier, schedule, budget, validation.
- Updates: only messages not in the ledger are read; only areas whose inputs changed are recomputed (verified against
  a full recompute, with a cascade fallback). The printing-bill practice update costs 1 call and touches budget only.
- Unknown constraint kinds cost at most one extra area-tagged call per area, and code re-validates the answer.

## Stack notes (please read)

- The task asks for FastAPI. **The build environment had no network, so FastAPI could not be installed.** `server.py`
  uses Starlette, the toolkit FastAPI is built on; routes and JSON are the same. Porting to FastAPI is mechanical.
- **The Gemini backend (`google-genai`) is untested**: the package was not installable here. It is written against the
  SDK and imports it lazily. The OpenAI-compatible path (Ollama etc.) was tested end to end against a local fake server
  whose token counts are **synthetic**. Run `--selftest` and the sample pack with your real model before the event.
- Offline tests use a stub model with canned reader outputs that live in `tests/` only.

## Open questions to confirm with organizers

1. **Small-model list.** Not published in the PDF. Ask at the help desk or in the all-teams WhatsApp group, then
   fill `.env`. Until then, localhost models are the safe path.
2. **Message file format.** Unknown. The splitter handles many layouts and falls back to treating the file as one blob
   for the model to split, but confirm the real format early.
3. **Code freeze time.** The document says 1:25, the organizer announced 2:00, and we were told to treat it as 1:50.
   The volunteer checks `git log -1` against the submitted hash at the 1:30 test run, so push before the test run
   whichever freeze time applies.
4. **Are unused confirmed judges paid?** The planner pays only the judges it uses (needed = ceil(teams / max per
   judge)). If all confirmed judges must be paid, honoraria and the headcount change; this is one rule in
   `engine/planner.py`.

## Tests

`python -m pytest tests -q` runs offline. It covers the splitter, grounding, resolver gates, planner, validator, trace,
orchestrator rules, model filter/fallback/timeouts, the sample pack, four synthetic packs (Roman Urdu, lakh/k, JSON/CSV,
power backup, vegetarian share, tax, fixed prayer time, unavailable hall, contingency, delivery fee, judge hours),
adversarial updates, the API, and a grep test that fails if pack numbers, message text or model names appear in
`engine/` or `ui/index.html`.
