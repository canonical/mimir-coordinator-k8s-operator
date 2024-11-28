set export  # Just variables are exported to Env variables
set positional-arguments  # Pass positional arguments to recipes

project-dir := invocation_directory()
src-dir := project-dir + "/src"
tests-dir := project-dir + "/tests"
all-dirs := src-dir + " " + tests-dir

PYTHONPATH := project-dir + ":" + \
	project-dir + "/src:" + \
	project-dir + "/lib"
PY_COLORS := "1"

# List the available recipes
@default:
	just --list

# Update uv.lock with the latest deps
@lock:
	uv lock --upgrade --no-cache

# Generate requirements.txt from pyproject.toml
@generate-requirements:
	uv export --frozen --no-hashes --format=requirements-txt > requirements.txt

# Lint the code
@lint:
	uv tool run ruff check {{all-dirs}}
	uv tool run ruff format --check --diff {{all-dirs}}

# Run static checks
@static:
	uv run --extra static pyright {{src-dir}}

# Format the code
@fmt:
	uv tool run ruff check --fix-only {{all-dirs}}
	uv tool run ruff format {{all-dirs}}

# Run unit tests
@unit *args='':
	uv run --isolated \
	    --extra unit \
		coverage run \
		--source={{src-dir}} \
		-m pytest \
		--ignore={{tests-dir}}/integration \
		--tb native \
		-v \
		-s \
		"$@"
	uv run --extra unit coverage report

# Run integration tests
@integration *args='':
	charmcraft pack
	uv run --isolated \
		--extra integration \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		{{tests-dir}}/spread \
		"$@"
