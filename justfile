set export
set positional-arguments
# pos-args are tricky because we can run:
# 	just lint unit --help
# 	since lint has no pos-args, but we cannot run another test after unit since it accepts pos-args

src-dir := "src"
tests-dir := "tests"
all-dirs := src-dir + " " + tests-dir

# PYTHONPATH := $(PROJECT):$(PROJECT)/lib:$(SRC)
PY_COLORS := "1"

@default:
	just --list

@lock:
	uv lock --upgrade --no-cache

@generate-requirements:
	rm -f requirements*.txt
	uv pip compile -q --no-cache pyproject.toml -o requirements.txt

@all-fast: lint static fmt unit

@lint:
	uv tool run ruff check {{all-dirs}}
	uv tool run ruff format --check --diff {{all-dirs}}

@static:
	uv run --extra static pyright

@fmt:
	uv tool run ruff check --fix-only {{all-dirs}}
	uv tool run ruff format {{all-dirs}}

@unit *args='':
	uv run --isolated \
	    --extra unit \
		coverage run \
		--source={{src-dir}} \
		-m pytest \
		--tb native \
		-v \
		-s \
		"$@"
	uv run --extra unit coverage report

@scenario *args='':
	echo "Add scenario tests here ..."
	# uv run --isolated \
	# 	--extra scenario \
	# 	coverage run \
	# 	--source={{src-dir}}  \
	# 	-m pytest \
	# 	--tb native \
	# 	-v \
	# 	-s \
	# 	{{tests-dir}}/scenario \
	# 	"$@"
	# uv run --extra scenario coverage report

@integration *args='':
	uv run --isolated \
		--extra integration \
		pytest \
		-v \
		-x \
		-s \
		--tb native \
		--log-cli-level=INFO \
		{{tests-dir}} /integration \
		"$@"
