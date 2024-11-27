set export  # Just variables are exported to Env variables
set quiet  # Don't print the recipes as they execute

project-dir := invocation_directory()
src-dir := project-dir + "/src"
tests-dir := project-dir + "/tests"
all-dirs := src-dir + " " + tests-dir

PYTHONPATH := project-dir + ":" + \
	project-dir + "/src:" + \
	project-dir + "/lib"
PY_COLORS := "1"

[doc('List the available recipes')]
default:
	just --list

[group('pre')]
[doc('Update uv.lock with the latest deps')]
lock:
	uv lock --upgrade --no-cache

[group('pre')]
[doc('Generate requirements.txt from pyproject.toml')]
requirements:
	rm -f requirements*.txt
	uv export --format=requirements-txt > requirements.txt

[group('checks')]
[doc('Lint the code')]
lint:
	uv tool run ruff check {{all-dirs}}
	uv tool run ruff format --check --diff {{all-dirs}}

[group('checks')]
[doc('Run static checks')]
static:
	uv run --extra static pyright {{src-dir}}

[group('checks')]
[doc('Format the code')]
fmt:
	uv tool run ruff check --fix-only {{all-dirs}}
	uv tool run ruff format {{all-dirs}}

[group('tests')]
[doc('Run unit tests')]
[positional-arguments]
unit *args='':
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

[group('tests')]
[doc('Run integration tests')]
integration *args='':
	uv run --isolated \
		--extra integration \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		{{tests-dir}}/integration \
		"$@"
