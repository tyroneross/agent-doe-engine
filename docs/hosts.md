# Call DOE from any model host

The core engine does not select or call an LLM. Any model host that can execute a
process can use the same commands and JSON files. A host with function tools can
wrap these commands in its own tool handler. The model chooses hypotheses and
interprets evidence; Python generates designs, validates records and computes
statistics. Chat interfaces without a tool executor cannot run the engine.

## Install and invoke

Use Python 3.10 or newer on macOS or Linux. The ledger uses POSIX file locks;
Windows users need a POSIX environment such as WSL. Native Windows is not covered.

From a checkout, install into the Python environment used by the host:

```bash
python3 -m pip install .
agent-doe-engine --help
agent-doe-engine doe detect 4
agent-doe-engine loop --summary --workdir /absolute/path/to/consumer
```

These calls work outside the engine checkout. If its executable directory is not
on PATH, use that environment's `python -m engine_cli` instead. Existing
`agent-doe-engine-doe`, `agent-doe-engine-analyst` and other entry points remain
available.

For a plugin or source checkout without installing the Python package, install
`requirements.txt`, resolve the engine root from the loaded skill or manifest,
and use an absolute path:

```bash
export DOE_ENGINE_ROOT="/absolute/path/to/agent-doe-engine"
python3 "$DOE_ENGINE_ROOT/scripts/engine_cli.py" doe detect 4
```

`DOE_ENGINE_ROOT` is the directory containing `scripts`, `skills` and `AGENTS.md`.
Claude Code can supply it from its nonempty `CLAUDE_PLUGIN_ROOT`. Other hosts must
resolve their own installation path. The npm artifact contains plugin assets and
Python source; npm installation does not install Python dependencies or a Node CLI.

## Command map

| Subcommand | Existing Python script | Purpose |
|---|---|---|
| `doe` | `doe.py` | Detect, generate, analyze and confirm |
| `analyst` | `analyst.py` | Qualify plans and amendments |
| `paired` | `paired_analysis.py` | Compare paired responses |
| `ledger` | `experiment_ledger.py` | Retain attempts and decisions |
| `report` | `report.py` | Render HTML, CSV and Markdown |
| `assess` | `semantic_assessor.py` | Optional semantic assessment |
| `loop` | `loop.py` | Single-variable experiment state |
| `metric` | `metric_runner.py` | Execute measurements and guards |
| `suggest-factors` | `suggest_factors.py` | Find candidate settings |
| `validate-factors` | `validate_factors.py` | Check adjustability |
| `worktree` | `worktree.py` | Isolate consumer changes |

Call `agent-doe-engine <command> --help` for its arguments. The dispatcher preserves
the existing tool's stdout, stderr and exit code. Successful data commands emit
JSON; help emits text. Treat a nonzero exit code as a failed invocation and retain
stderr. Do not treat missing measurements as zero.

## Host responsibilities

- Load `AGENTS.md` and `agents/statistical-analyst.md`. Read the consumer system's
  purpose and protected outcomes before proposing factors.
- Pass argument arrays to process APIs, preserving JSON as one argument. Use a
  controlled working directory and retain the exact command, inputs and outputs.
- Metric and guard commands execute code. Apply the host's authorization policy
  before executing user or model supplied commands; do not expose an unrestricted
  shell to an untrusted remote caller.
- Keep failed attempts, freeze each batch, and require confirmation and review
  before promotion. CLI access does not establish experimental effectiveness.
- TypeSafe/Jev remains disabled unless configured with cloud permission and the
  user's key. No default provider or automatic provider fallback is required.

This is a CLI integration contract, not a bundled MCP server. MCP hosts can expose
it through their process tool adapter. Claude and Codex plugin manifests are
included; other hosts use this interface. Verification covers deterministic CLI
behavior and installed artifacts, not every model's reasoning or every host UI.
The separate `experiments/goal-alignment` dogfood runner is Claude-specific and is
not required to operate the engine.
