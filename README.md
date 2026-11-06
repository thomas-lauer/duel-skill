# The Duel skill (Codex variant)

**Make Claude defend its answer against another model.** The Duel, also called Face Off, sends an
answer to an OpenAI model for a critique. Claude defends what holds up, revises what does not, and
returns the answer with a record of what changed.

Say **“Duel this with ChatGPT”** or run `/duel`. Up to three rounds. Two actual models. One clear
account of the claims they challenged.

This fork runs the critic through the **Codex CLI** and your own Codex login, for example your
ChatGPT plan. It reads and sends no API key. Claude Code is the defender and writes the final
synthesis. There is no neutral judge and no guarantee the resulting answer is correct.

The original by Jake Schincariol is [Jakeschincariol/duel-skill](https://github.com/Jakeschincariol/duel-skill).
It calls the OpenAI Responses API with `OPENAI_API_KEY`. Use the original if you prefer API billing.

## What this fork changes

| Area | Original | This fork |
|---|---|---|
| Critic access | OpenAI Responses API, `OPENAI_API_KEY` | `codex exec` under your Codex login, no API key |
| Billing | OpenAI API usage | Codex plan limits (for example a ChatGPT plan) |
| Credentials | Key read from the environment and sent as a Bearer header | No credential handling; Codex receives only a fixed list of named environment variables |
| Critic tools | None sent | Codex is an agent: tool families switched off, read-only sandbox, empty working folder, run stopped if it still attempts a tool call |
| Prompt overhead | About 400 tokens of critic instructions | About 4,500 tokens after trimming, down from about 15,000 (see [Token overhead](#token-overhead)) |
| Output cap | `--max-output-tokens` (default 8,192) | None per call; the timeout bounds the run (default 300 seconds) |
| Model in the result | Model returned by the API | Requested model, marked `model_verified: false`, because Codex events do not name the serving model |
| Manual mode | Dry run of the API request | Dry run shows the Codex command, the critic instructions and the stdin text |
| Windows | `python3` only | Docs mention `python`; one upstream test that cannot run on Windows is skipped there |

Details of the changes:

- **`skills/duel/scripts/duel.py`** replaces the HTTP client with one `codex exec --json` run per
  critique. The packet goes in on stdin, never on the command line: packets may hold up to 100,000
  characters, which exceeds the Windows command-line limit, and command lines are readable by other
  processes of the same user.
- **Isolation of the critic.** The run is `--ephemeral`, uses `--ignore-user-config` and
  `--ignore-rules`, works in `--sandbox read-only` inside a new empty temporary folder and switches
  off web search and the shell, code-mode, browser, computer-use, app, plugin, image, hook, goal and
  sub-agent features. Codex cannot remove every tool definition, so the helper watches the JSONL
  events and kills the process as soon as an item other than a message, reasoning or a warning
  appears. The critique of such a run is discarded.
- **Environment allowlist.** Codex receives only named variables: system paths, the user profile,
  `CODEX_HOME`, locale, temp folders, proxy and certificate settings. `OPENAI_API_KEY`,
  `CODEX_API_KEY`, `OPENAI_BASE_URL` or any other secret in your shell never reach Codex, so the run
  always uses your Codex login.
- **Error mapping.** Codex errors become short, content-free messages: model needs a newer CLI
  (`codex update`), model not available for this login, not logged in (`codex login`), usage or rate
  limit reached. The helper itself never retries.
- **`check`** reports whether Codex is installed and logged in, without sending a critique.
- **Removed:** the API backend, `--max-output-tokens`, the redirect guard and the HTTP response
  parser. Removing the credential path also took the skill from a SkillSpector score of 100
  (CRITICAL, credential sent over the network) to 31 (MEDIUM, the remaining findings are the
  intended `subprocess` calls to Codex).
- **Tests** cover the Codex path offline with a fake process: command flags, stdin packet, empty
  working folder, environment allowlist, event parsing, tool-attempt abort, error mapping, timeouts
  and output reservation. CI needs neither Codex nor credentials.

## Token overhead

### The problem

The original sends about 400 tokens of critic instructions (1,466 characters) plus the packet. Codex is a coding agent,
and `codex exec` wraps every request in its full agent context. For a pure text critique most of it
is dead weight, and on a Codex plan it counts against your usage limits on every round.

Measured on Windows with `codex-cli 0.167.0` and `gpt-6-astra`, using the `input_tokens` that Codex
reports at `turn.completed`:

| Setup | Prompt | Input tokens |
|---|---|---:|
| Plain `codex exec`, user config ignored | two-word test prompt | 20,837 |
| Tool families switched off | `examples/pricing.packet.json` | 15,139 |
| Tool families off, agent prompt replaced, sections trimmed (this fork) | `examples/pricing.packet.json` | 5,467 |

The critique from the last setup covered the same points as before and was of similar length. A full
three-round duel therefore costs about 16,500 input tokens plus packets instead of about 45,000.

### Where the overhead comes from

`codex debug prompt-input` renders the model-visible prompt without calling the model. Broken down
by section (characters, measured with the user config loaded, so the absolute figures differ a
little from a `--ignore-user-config` run):

| Section | Characters | Handling in this fork |
|---|---:|---|
| List of installed Codex skills | ~22,100 | removed with `skills.include_instructions=false` |
| Built-in Codex agent instructions | ~21,200 | replaced by the critic instructions via `model_instructions_file` |
| Tool definitions (`exec`, `wait`, `request_user_input`, collaboration tools) | ~17,900 | remain; no switch found in 0.167.0 |
| Multi-agent role and mode sections | ~2,700 | remain |
| Global `~/.codex/AGENTS.md` | depends on the user | remains; loaded even with `--ignore-user-config` |
| Permission, collaboration, app and environment sections | ~1,900 | removed with the `include_*` settings |

The skill list grows with every Codex skill you install, so the saving is larger on machines with
many skills.

### What remains and the trade-offs

- **About 4,500 tokens per call stay.** They are mostly tool definitions that Codex 0.167.0 includes
  even when the matching features are off. Feature flags such as `code_mode_host` make those tools
  fail closed, but their descriptions still go to the model.
- **Your global `~/.codex/AGENTS.md` is always part of the prompt.** Its rules can shape the critique,
  for example its language or stock phrases. The only way around it would be a separate
  `CODEX_HOME`, which would require copying login data; this fork deliberately does not do that.
- **The switches are version-specific.** `model_instructions_file`, `skills.include_instructions` and
  the `include_*` settings exist in `codex-cli 0.167.0`. A future Codex version may rename them or
  add new sections. Re-measure with `codex debug prompt-input` after a Codex update.
- **Cached input.** Codex reports part of the input as `cached_input_tokens` (3,456 of 5,467 in the
  measurement above). How a plan counts cached tokens is up to OpenAI.

## Install

Copy the skill folder into your personal skills:

```bash
git clone https://github.com/thomas-lauer/duel-skill.git
mkdir -p ~/.claude/skills
cp -R duel-skill/skills/duel ~/.claude/skills/duel
```

For a project-only install, copy `skills/duel` into that project's `.claude/skills/` instead.

Or install as a Claude Code plugin:

```text
/plugin marketplace add thomas-lauer/duel-skill
/plugin install duel-skill@duel-skill
```

Plugin installs use `/duel-skill:duel`. The copied skill uses `/duel`.

Requires **Claude Code, the Codex CLI and Python 3.9 or newer**. The helper uses Python's standard
library; there is nothing to pip install.

## Connect the critic

Install the [Codex CLI](https://developers.openai.com/codex/cli), run `codex login` yourself, and
keep it current with `codex update`: new models need a recent CLI. Each critic call is one
`codex exec` run and counts against your Codex plan limits.

No Anthropic API key is needed: the current Claude Code session is the defender.

The default critic is `gpt-6-astra`. Set `DUEL_OPENAI_MODEL` to use a different model available to
your Codex login, or pass `--model` to `/duel` or the Python helper.

The skill is free under the MIT license. Claude Code usage and Codex plan usage fall under their
respective plans. The normal run makes up to three critic calls; `--quick` makes one.

## Use it

```text
/duel
/duel --quick
/duel --manual --quick
/duel --rounds 2 Should I raise my coaching price from $500 to $700?
Duel this with ChatGPT.
Face off this answer.
```

On its own, `/duel` uses the answer you want challenged and its original task. With a new task,
Claude first writes an answer. Three rounds is the default; `--quick` selects one, and `--rounds`
accepts 1, 2, or 3. A run can end early if the critique adds nothing substantive.

1. **Prepare.** Claude captures the task, answer, relevant context, and claims in a focused packet.
2. **Challenge.** The OpenAI critic identifies specific weaknesses, counterexamples, missing
   evidence, and conditions that could change the answer.
3. **Defend and revise.** Claude addresses the objections, updates its answer, and carries the
   debate forward to the next round.
4. **Return the result.** You get the revised answer and a claim ledger: **retained**, **revised**,
   **dropped**, or **unresolved**. Claims keep stable IDs so concessions remain visible.

Claude makes the final synthesis, and disagreement may remain unresolved. Debate is a way to
surface weaknesses; it is not independent verification of every fact.

Without Codex, use manual mode: have an external model critique the prepared packet yourself, then
paste its actual response back into Claude. The skill must label this manual mode and cannot invent
an external model's participation.

## What gets sent and saved

The helper sends only the prepared packet and its critic instructions to OpenAI, through one
`codex exec` run. It does not upload the repository or conversation wholesale. Review the packet
before using sensitive material, and include only the context needed for the task. Each helper
invocation makes one critic call and never retries on its own. Packets are limited to 128 KiB (and
100,000 text characters). The data policies of your Codex/ChatGPT account apply.

Local run records live under `.duel/`, which this repository ignores. Keep real run packets and
responses out of commits, including when installing the skill into another project.

## Helper and offline checks

Run these from the repository. On Windows, use `python` where `python3` is not available.

```bash
python3 skills/duel/scripts/duel.py check
mkdir -p .duel
python3 skills/duel/scripts/duel.py critique \
  --packet examples/pricing.packet.json \
  --output .duel/example.request.json --dry-run
python3 -m unittest discover -s tests -v
```

The example packet is illustrative offline input, not a live duel or a model verdict. A dry run
writes the planned Codex call without running it. `check` sends no critique; it exits with status 1
when Codex is missing or not logged in. To make one real critic call:

```bash
mkdir -p .duel/example
python3 skills/duel/scripts/duel.py critique \
  --packet examples/pricing.packet.json \
  --output .duel/example/critique.json
```

The helper also accepts `--model` and `--timeout`. It handles one critique; the
[skill](skills/duel/SKILL.md) orchestrates the rounds and final answer.

CI runs the offline unit tests on Python 3.9, 3.12, and 3.14, without Codex.

## License

[MIT](LICENSE). Original by Jake Schincariol; Codex variant by Thomas Lauer.
