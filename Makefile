PROJECT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

SRC := $(PROJECT)src
TESTS := $(PROJECT)tests
ALL := $(SRC) $(TESTS)

export PYTHONPATH = $(PROJECT):$(PROJECT)/lib:$(SRC)

lock:
	uv lock --upgrade --no-cache

lint:
	uv tool run ruff check $(ALL)
	uv tool run ruff format --check --diff $(ALL)
	uv run --extra dev pyright

fmt:
	uv tool run ruff check --fix-only $(ALL)
	uv tool run ruff format $(ALL)

unit:
	uv run --isolated \
	    --extra dev \
		coverage run \
		--source=$(SRC) \
		-m pytest \
		--ignore=$(TESTS)/integration \
		--tb native \
		-v \
		-s \
		$(ARGS)
	uv run --all-extras coverage report

integration:
	uv run --all-extras \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		$(TESTS)/integration \
		$(ARGS)
