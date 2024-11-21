PROJECT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

SRC := $(PROJECT)src
TESTS := $(PROJECT)tests
ALL := $(SRC) $(TESTS)

export PYTHONPATH = $(PROJECT):$(PROJECT)/lib:$(SRC)
export PY_COLORS=1

lock:
	uv lock --upgrade --no-cache

lint:
	uv tool run ruff check $(ALL)
	uv tool run ruff format --check --diff $(ALL)

static:
	uv run --extra static pyright

fmt:
	uv tool run ruff check --fix-only $(ALL)
	uv tool run ruff format $(ALL)

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

scenario:
	echo "TODO: implement scenario tests with UV."

integration:
	uv run --extra integration \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/integration \
		$(ARGS)
