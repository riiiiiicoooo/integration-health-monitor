.PHONY: help install dev test lint format migrate seed run demo docs clean

# Colors for output
CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

help:
	@echo "$(CYAN)Integration Health Monitor — Development Commands$(RESET)"
	@echo ""
	@echo "$(GREEN)Setup:$(RESET)"
	@echo "  make install       Install dependencies"
	@echo "  make migrate       Run database migrations"
	@echo "  make seed          Seed database with sample data"
	@echo ""
	@echo "$(GREEN)Development:$(RESET)"
	@echo "  make dev           Run API server locally (port 8000)"
	@echo "  make demo          Run 24-hour simulation demo"
	@echo "  make lint          Check code quality"
	@echo "  make format        Format code with black"
	@echo "  make test          Run tests"
	@echo ""
	@echo "$(GREEN)Docker:$(RESET)"
	@echo "  make build         Build Docker images"
	@echo "  make up            Start services (docker-compose up)"
	@echo "  make down          Stop services (docker-compose down)"
	@echo "  make ps            Show running services"
	@echo ""
	@echo "$(GREEN)Database:$(RESET)"
	@echo "  make db-shell      Connect to PostgreSQL"
	@echo "  make db-reset      Drop and recreate database"
	@echo ""
	@echo "$(GREEN)Documentation:$(RESET)"
	@echo "  make docs          Build and serve documentation"
	@echo "  make report        Generate sample scorecard report"
	@echo ""
	@echo "$(GREEN)Cleanup:$(RESET)"
	@echo "  make clean         Remove cache and build artifacts"

# ============================================================================
# Setup Commands
# ============================================================================

install:
	@echo "$(CYAN)Installing dependencies...$(RESET)"
	pip install -r requirements.txt

dev:
	@echo "$(CYAN)Starting API server on http://localhost:8000$(RESET)"
	@echo "$(YELLOW)Swagger docs: http://localhost:8000/docs$(RESET)"
	cd /sessions/youthful-eager-lamport/mnt/Portfolio/integration-health-monitor && python -m uvicorn api.app:app --reload --host 0.0.0.0 --port 8000

demo:
	@echo "$(CYAN)Running 24-hour integration incident simulation...$(RESET)"
	@echo "$(YELLOW)This will take ~5 minutes$(RESET)"
	python demo/simulate_24h.py

test:
	@echo "$(CYAN)Running tests...$(RESET)"
	pytest tests/ -v --cov=src --cov=api --cov-report=html
	@echo "$(GREEN)Coverage report generated: htmlcov/index.html$(RESET)"

lint:
	@echo "$(CYAN)Running linters...$(RESET)"
	flake8 src/ api/ demo/ --max-line-length=100 --ignore=E203,W503
	mypy src/ api/ --ignore-missing-imports
	@echo "$(GREEN)Linting complete$(RESET)"

format:
	@echo "$(CYAN)Formatting code with black...$(RESET)"
	black src/ api/ demo/ --line-length=100
	@echo "$(GREEN)Formatting complete$(RESET)"

# ============================================================================
# Database Commands
# ============================================================================

migrate:
	@echo "$(CYAN)Running database migrations...$(RESET)"
	@echo "Applying migration 001_initial_tables.sql"
	psql -h localhost -U postgres -d integration_monitor -f schema/migrations/001_initial_tables.sql
	@echo "Applying migration 002_anomaly_detection.sql"
	psql -h localhost -U postgres -d integration_monitor -f schema/migrations/002_anomaly_detection.sql
	@echo "Applying migration 003_funnel_correlation.sql"
	psql -h localhost -U postgres -d integration_monitor -f schema/migrations/003_funnel_correlation.sql
	@echo "$(GREEN)Migrations complete$(RESET)"

seed:
	@echo "$(CYAN)Seeding database with sample data...$(RESET)"
	psql -h localhost -U postgres -d integration_monitor -f schema/seed.sql
	@echo "$(GREEN)Seed data loaded$(RESET)"

db-shell:
	@echo "$(CYAN)Connecting to PostgreSQL...$(RESET)"
	psql -h localhost -U postgres -d integration_monitor

db-reset:
	@echo "$(YELLOW)WARNING: This will drop and recreate the database$(RESET)"
	@echo "Are you sure? [y/N]" && read ans && [ $${ans:-N} = y ]
	dropdb -h localhost -U postgres integration_monitor 2>/dev/null || true
	createdb -h localhost -U postgres integration_monitor
	$(MAKE) migrate
	$(MAKE) seed
	@echo "$(GREEN)Database reset complete$(RESET)"

# ============================================================================
# Docker Commands
# ============================================================================

build:
	@echo "$(CYAN)Building Docker images...$(RESET)"
	docker-compose build

up:
	@echo "$(CYAN)Starting services...$(RESET)"
	docker-compose up -d
	@echo "$(GREEN)Services started$(RESET)"
	@echo "$(CYAN)Dashboard: http://localhost:3000$(RESET)"
	@echo "$(CYAN)API docs: http://localhost:8000/docs$(RESET)"
	@echo "$(CYAN)PostgreSQL: localhost:5432$(RESET)"

down:
	@echo "$(CYAN)Stopping services...$(RESET)"
	docker-compose down

ps:
	@echo "$(CYAN)Running services:$(RESET)"
	docker-compose ps

logs:
	docker-compose logs -f

# ============================================================================
# Documentation Commands
# ============================================================================

docs:
	@echo "$(CYAN)Building documentation...$(RESET)"
	mkdocs serve

report:
	@echo "$(CYAN)Generating sample scorecard report...$(RESET)"
	python -c "from src.scorecard_report import ScorecardReportGenerator; \
	from samples.provider_scorecard_report import sample_scorecards; \
	report = ScorecardReportGenerator.generate_report(sample_scorecards); \
	with open('samples/provider_scorecard_report.md', 'w') as f: f.write(report); \
	print('Report generated: samples/provider_scorecard_report.md')"

# ============================================================================
# Cleanup
# ============================================================================

clean:
	@echo "$(CYAN)Cleaning up cache and artifacts...$(RESET)"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/ .mypy_cache/ htmlcov/ .coverage
	rm -rf build/ dist/ *.egg-info/
	@echo "$(GREEN)Cleanup complete$(RESET)"

# ============================================================================
# Quick Commands
# ============================================================================

.PHONY: quick-start
quick-start: install migrate seed dev

.PHONY: full-demo
full-demo: install build up
	@echo "$(CYAN)Full demo setup complete$(RESET)"
	@echo "Services running:"
	@docker-compose ps
	@echo ""
	@echo "$(YELLOW)Next steps:$(RESET)"
	@echo "1. Run the simulation: make demo"
	@echo "2. View the dashboard: http://localhost:3000"
	@echo "3. API docs: http://localhost:8000/docs"
	@echo "4. Connect to DB: make db-shell"
