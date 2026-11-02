# Pick who is asking with WHO (names live in config/users.yaml), e.g. `make run WHO=levis_manager`.
WHO ?= ceo

.PHONY: run run_logs ui

run:
	uv run retail-agent --user $(WHO)

run_logs:
	uv run retail-agent --user $(WHO) --verbose

ui:
	uv run --extra ui streamlit run src/retail_agent/ui.py
