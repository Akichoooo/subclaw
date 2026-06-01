# subclaw - Multi-Model LLM Gateway
# https://github.com/Akichoooo/subclaw
#
# Common development tasks. Run `make help` for a list of targets.

.PHONY: help install run test test-fast lint format clean docker docker-up docker-down docker-logs example-keys install-skill uninstall-skill

PYTHON ?= python3
PIP ?= pip3
PROXY_DIR := proxy
PORT ?= 4748

help:   ## Show this help.
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:   ## Install Python dependencies into the active virtualenv.
	cd $(PROXY_DIR) && $(PIP) install -r requirements.txt

run:   ## Start the proxy natively (uses active venv).
	cd $(PROXY_DIR) && $(PYTHON) app.py

run-dev:   ## Start the proxy with debug logging.
	cd $(PROXY_DIR) && LOG_LEVEL=DEBUG $(PYTHON) app.py

test:   ## Run the test suite (placeholder until tests are added).
	@echo "No tests yet — see CONTRIBUTING.md for how to add them."

lint:   ## Run ruff + black --check on proxy code.
	@command -v ruff >/dev/null 2>&1 || $(PIP) install ruff black
	cd $(PROXY_DIR) && ruff check app.py
	cd $(PROXY_DIR) && black --check app.py

format:   ## Auto-format proxy code.
	@command -v black >/dev/null 2>&1 || $(PIP) install black
	cd $(PROXY_DIR) && black app.py

clean:   ## Remove Python cache and build artifacts.
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true

docker:   ## Build the Docker image.
	docker build -t akichoooo/subclaw:latest .

docker-up:   ## Start the proxy via docker-compose.
	docker compose up -d
	@echo "Proxy running at http://localhost:$(PORT)"
	@echo "Try: curl http://localhost:$(PORT)/health"

docker-down:   ## Stop the docker-compose stack.
	docker compose down

docker-logs:   ## Tail logs from the proxy container.
	docker compose logs -f subclaw

example-keys:   ## Copy keys.example.json to keys.json (you still need to edit it).
	@if [ ! -f $(PROXY_DIR)/keys.json ]; then cp $(PROXY_DIR)/keys.example.json $(PROXY_DIR)/keys.json && echo "Created $(PROXY_DIR)/keys.json — edit it before running."; else echo "$(PROXY_DIR)/keys.json already exists. Skipping."; fi

install-skill:   ## Install the /subclaw slash command for Claude Code.
	@mkdir -p $$HOME/.claude/commands $$HOME/.claude/scripts
	cp cli-skills/claude/subclaw.md $$HOME/.claude/commands/subclaw.md
	cp cli-skills/run-claw-pool.sh $$HOME/.claude/scripts/run-claw-pool.sh
	chmod +x $$HOME/.claude/scripts/run-claw-pool.sh
	@echo "Slash command installed. Restart Claude Code and try /subclaw."

uninstall-skill:   ## Remove the /subclaw slash command.
	rm -f $$HOME/.claude/commands/subclaw.md
	rm -f $$HOME/.claude/scripts/run-claw-pool.sh
	@echo "Slash command removed."

stats:   ## Curl the proxy /stats endpoint.
	@curl -sS http://localhost:$(PORT)/stats | $(PYTHON) -m json.tool

health:   ## Curl the proxy /health endpoint.
	@curl -sS http://localhost:$(PORT)/health | $(PYTHON) -m json.tool

dashboard:   ## Open the dashboard in your default browser.
	@command -v xdg-open >/dev/null 2>&1 && xdg-open http://localhost:$(PORT)/dashboard || open http://localhost:$(PORT)/dashboard
