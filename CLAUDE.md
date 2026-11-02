# Retail data agent

CLI chat agent that answers retail analytics questions over BigQuery
(`thelook_ecommerce`). Prototype for an assessment.

## Sources of truth
- `AI Technical Assignment - Retail Company.pdf`: what is asked.
- `docs/plan.md`: build order, client answers, design decisions. Read it before starting a step.
- `tasks/`: one file per small step. Work on one task at a time.

## Commands
- Install: `uv sync` (add `--extra anthropic` to use Claude)
- Run: `uv run retail-agent --user ceo` (names live in `users.yaml`)
- Test: `uv run pytest`
- Settings come from `.env` (template: `.env.example`). Never commit `.env`.

## How we build
- Thin slices: each change leaves the agent working end to end. Extend, don't rewrite.
- Plain, obvious code. Full-word names. Before adding a new helper, class
  or layer, show a short before/after and wait for a yes.
- Config stays out of code (`.env`, `users.yaml`, `schema.yaml`, `prompts/`).
- Build only what the plan marks as "built". Design-only parts
  (Golden Knowledge, learning loop, evals, persona updates) go in docs.

## Rules that must not break
- PII never reaches the LLM or the output. Mask it before the model call.
- Users see only their own brands. The CEO sees everything.
- Default model is Gemini on a free key, within ~2–5 LLM calls per question.
  Local dev may use Claude, but don't commit a non-Gemini default.
- Every query goes through the guard, with the bytes-billed cap.

## Docs
- When code changes, say which `docs/html/*.html` explainers are now stale.
- Explainers live in `docs/html/` and stay local (git-ignored). The repo keeps PDFs of four
  in `docs/pdf/`; regenerate a PDF when its page changes.
