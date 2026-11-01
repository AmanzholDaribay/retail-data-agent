# Pick who is asking with WHO (names live in users.yaml), e.g. `make run WHO=levis_manager`.
WHO ?= ceo

.PHONY: run run_logs

run:
	uv run retail-agent --user $(WHO)

run_logs:
	uv run retail-agent --user $(WHO) --verbose
