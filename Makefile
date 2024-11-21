PROJECT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

SRC := $(PROJECT)src
TESTS := $(PROJECT)tests
ALL := $(SRC) $(TESTS)

export PYTHONPATH = $(PROJECT):$(PROJECT)/lib:$(SRC)

update-dependencies:
	uv lock -U --no-cache

generate-requirements: clean-requirements
	uv pip compile -q --no-cache pyproject.toml -o requirements.txt

lint:
	uv tool run ruff check $(ALL)
	uv tool run ruff format --check --diff $(ALL)
	uv run --extra dev pyright

fmt:
	uv tool run ruff check --fix-only $(ALL)
	uv tool run ruff format $(ALL)

# TODO: What about charm-lib

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

clean: clean-charm clean-requirements clean-other

clean-charm:
	rm -f *.charm

clean-requirements:
	rm -f requirements*.txt

clean-other:
	rm -rf .coverage
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf .venv
	rm -rf *.rock
	rm -rf **/__pycache__
	rm -rf **/*.egg-info