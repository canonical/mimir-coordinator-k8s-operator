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
	uv run --frozen --isolated --extra dev \
		ruff check $(ALL)
	uv run --frozen --isolated --extra dev \
		ruff format --check --diff $(ALL)

# Run static checks
static:
	uv run --frozen --isolated --extra dev pyright

# Format the code
fmt:
	uv run --frozen --isolated --extra dev \
		ruff check --fix-only $(ALL)
	uv run --frozen --isolated --extra dev \
		ruff format $(ALL)

# Run unit and scenario tests
unit:
	uv run --frozen --isolated --extra dev \
		coverage run \
		--source=$(SRC) \
		-m pytest \
		--tb native \
		--verbose \
		--capture=no \
		$(TESTS)/unit \
		$(ARGS)
	
	uv run --frozen --isolated --extra dev \
		coverage run \
		--source=$(SRC) \
		--append \
		-m pytest \
		--tb native \
		--verbose \
		--capture=no \
		$(TESTS)/scenario \
		$(ARGS)
	uv run --frozen --isolated --extra dev \
		coverage report

# Run integration tests
integration:
	uv run --frozen --isolated --extra dev \
		pytest \
		--verbose \
		--exitfirst \
		--capture=no \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/integration \
		$(ARGS)

# Run interface tests
interface:
	uv run --frozen --isolated --extra dev \
		pytest \
		--verbose \
		--exitfirst \
		--capture=no \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/interface \
		$(ARGS)