# 06: Other providers through config, and a setup guide for reviewers

**What to build:** a reviewer can go from cloning the repo to a first answer with only a free Gemini key from Google AI Studio and a GCP project, by following the README. Gemini is the default. Anthropic, OpenAI and Azure OpenAI stay available through config but are switched off: they're optional installs, and switching to one needs only `.env` changes. The README states clearly which provider is tested and which are only available.

Source: [step 1 spec](../docs/specs/step-1-core-loop.md).

**Blocked by:** 01 (Walking skeleton)

**Status:** deferred: moved out of step 1 on 2026-10-30. Done in step 7 (docs), so the README's example run shows the final behavior.

- [ ] The Gemini integration is a regular dependency. Anthropic and OpenAI (which also covers Azure OpenAI) are optional extras.
- [ ] Switching to Anthropic or OpenAI needs only installing the extra and changing `.env`, with no code changes.
- [ ] `.env.example` documents every setting with a short comment. Gemini is active, and the other providers' settings are present but commented out, including the extra values Azure OpenAI needs.
- [ ] The README covers:
  - what the agent does;
  - setup with `uv`, and with `pip install -r requirements.txt` as in the brief (the file is exported with `uv export`);
  - getting a free Gemini API key from Google AI Studio, and that the agent uses at most 5 LLM calls per question to fit the free tier;
  - GCP setup: a project (the free BigQuery sandbox is enough), Application Default Credentials or a service-account key file, and that queries are billed to `GCP_PROJECT`;
  - configuration;
  - an example run with sample output;
  - providers: Gemini is the default and tested; Anthropic, OpenAI and Azure OpenAI are available but switched off, with how to turn one on.
- [ ] Manual check (nice to have): the example question on Anthropic or OpenAI, switched on through `.env`, returns a sensible answer and its SQL.
