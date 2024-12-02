PROJECT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

SRC := $(PROJECT)src
TESTS := $(PROJECT)tests
ALL := $(SRC) $(TESTS)

export PYTHONPATH = $(PROJECT):$(PROJECT)/lib:$(SRC)
export PY_COLORS=1

# Update uv.lock with the latest deps
lock:
	uv lock --upgrade --no-cache

# Generate requirements.txt from pyproject.toml
requirements:
	uv export --frozen --no-hashes --format=requirements-txt -o requirements.txt

# Lint the code
lint:
	uv run --isolated --extra lint \
		codespell $(PROJECT) \
		--skip $(PROJECT).git \
		--skip $(PROJECT).venv \
		--skip $(PROJECT)build \
		--skip $(PROJECT)lib
	uv run --isolated --extra lint \
		ruff check $(ALL)
	uv run --isolated --extra lint \
		ruff format --check --diff $(ALL)

# Run static checks
static:
	uv run --extra static pyright

# Format the code
fmt:
	uv run --isolated --extra fmt \
		ruff check --fix-only $(ALL)
	uv run --isolated --extra fmt \
		ruff format $(ALL)

# Run unit tests
unit:
	uv run --isolated --extra unit \
		coverage run \
		--source=$(SRC) \
		-m pytest \
		--tb native \
		--verbose \
		--capture=no \
		$(TESTS)/unit \
		$(ARGS)
	uv run --isolated --extra unit \
		coverage report

# Run integration tests
integration:
	uv run --isolated --extra integration \
		pytest \
		--verbose \
		--exitfirst \
		--capture=no \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/integration \
		$(ARGS)
