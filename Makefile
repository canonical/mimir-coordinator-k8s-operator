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
generate-requirements:
	uv export --frozen --no-hashes --format=requirements-txt > requirements.txt

# Lint the code
lint:
	uv tool run ruff check $(ALL)
	uv tool run ruff format --check --diff $(ALL)

# Run static checks
static:
	uv run --extra static pyright

# Format the code
fmt:
	uv tool run ruff check --fix-only $(ALL)
	uv tool run ruff format $(ALL)

# Run unit tests
unit:
	uv run --isolated \
	    --extra unit \
		coverage run \
		--source=$(SRC) \
		-m pytest \
		--ignore=$(TESTS)/integration \
		--tb native \
		-v \
		-s \
		$(ARGS)
	uv run --extra unit coverage report

# Run integration tests
integration:
	uv run --isolated \
		--extra integration \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/integration \
		$(ARGS)
