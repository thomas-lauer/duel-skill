# Exchange protocol

The helper makes one `codex exec` run per call, under the local Codex CLI login.
Claude manages the local files, the rebuttal, and the final answer. No API key is
involved, neither OpenAI nor Anthropic.

## Packet

```json
{
  "task": "Should I raise my coaching price from $500 to $700?",
  "answer": "Yes. At $700 you make 40% more revenue from the same clients.",
  "context": "No retention or demand data has been supplied.",
  "round": 1,
  "history": [],
  "claims": [
    {"id": "C1", "text": "The new price produces 40% more total revenue."}
  ]
}
```

`task`, `answer`, `round`, and `history` are required. `context` and `claims` are
optional. Use nonempty strings for text fields, except context may be omitted when
there is nothing to add. Claims use unique, stable IDs. `round` must be 1, 2, or 3.

For round 2, history contains the completed first exchange:

```json
[
  {
    "round": 1,
    "critic": "The actual critique returned by the critic.",
    "response": "Claude's concise response to the objections and revised answer."
  }
]
```

For round 3, include rounds 1 and 2 in order. Preserve the critic's actual text;
do not rewrite it to imply agreement. The top-level answer is always the current
answer. Keep the original in `initial.md`. The packet has a 128 KiB byte limit and
a 100,000-character limit across its text fields;
if the helper rejects an oversized packet, narrow the task or context explicitly
instead of silently cutting relevant history.

## Run files

```text
.duel/<unique-run>/
  initial.md
  round-1.packet.json
  round-1.critic.json
  round-1.response.md
  round-2.packet.json       # only when needed
  round-2.critic.json
  round-2.response.md
  round-3.packet.json       # only when needed
  round-3.critic.json
  round-3.response.md
  final.md
```

The helper refuses to overwrite an output file. A successful result contains
`status`, `round`, `model`, `model_verified`, `response_id`, `critique`, and
`usage`. Codex events do not report the serving model, so `model` is the requested
one, `model_verified` is always `false`, and `response_id` is the Codex thread ID.
Preserve those metadata fields. Usage reports tokens, not a calculated cost; it
includes the agent's own prompt overhead. Round packet/reply files stay local;
only the prepared packet and critic instructions go to OpenAI through Codex.
Credentials never belong in a packet or run record. Codex receives only a fixed
list of named environment variables (system paths, its home, proxy settings),
so API keys and endpoint overrides in the parent environment never reach it.

## Controls

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/duel.py" critique \
  --packet ".duel/RUN/round-1.packet.json" \
  --output ".duel/RUN/round-1.request.json" \
  --dry-run
```

Dry-run validates the packet and writes the planned Codex call without running
Codex: the command line in `request.command`, the critic instructions in
`request.instructions`, and the stdin text in `request.stdin`. It is useful for
inspecting exactly what would leave the machine. It is not a model response and
does not complete a debate round.

Use `critique --help` for the available `--model` and `--timeout` controls. The
default timeout is 300 seconds. Codex has no per-call output cap, so the timeout
is the only bound. Do not raise it or repeat uncertain requests without the
user's agreement. The helper itself never retries.

### How the Codex call is bounded

The helper runs `codex exec --json --ephemeral --ignore-user-config --ignore-rules
--sandbox read-only` in a new empty temporary folder. The critic instructions
replace Codex's built-in agent prompt through `model_instructions_file`, written
to a temporary file next to that folder. The packet goes in on stdin, never on the
command line. The skill list, permission, collaboration, app, and environment
sections are left out of the prompt (`skills.include_instructions=false` and the
`include_*` settings). What remains is about 4,500 tokens of tool definitions and
the user's global `~/.codex/AGENTS.md`, which this Codex version always loads. It switches off web search and the shell, browser, computer-use,
app, plugin, image, and sub-agent tool families. If Codex still emits any item
other than a message, reasoning, or warning, the helper stops the process and
discards the critique. Codex runs count against the limits of the Codex login,
and the data policies of that account apply. The Codex CLI may retry transport
errors internally; the helper cannot rule that out.

## Manual mode

Generate a dry-run request file. Give the user `request.instructions` and the
packet text in `request.stdin` to paste into a separate model. Ask them to paste
that model's actual response back. Do not claim to have accessed their
ChatGPT account or verified which model they used. Save the supplied critique in
`round-N.manual.json`, separate from the helper's output format:

```json
{
  "mode": "manual",
  "round": 1,
  "origin": "user-provided",
  "model": "unknown",
  "verified": false,
  "critique": "The actual external response pasted by the user."
}
```

Replace `model` with the user's reported model only when supplied. Do not invent
response IDs or token usage. Continue the same claim/revision process, counting an
exchange after the actual critique and Claude's response are both present. Codex
is not needed. Manual responses must never be described as helper results; a
generated prompt alone completes zero exchanges.

## Worked example: the evidence boundary

For the illustrative coaching question above, raising price from 500 to 700 is a
40% price increase. Total revenue grows by 40% only if the paying client count is
unchanged. At 70% retention, revenue is `700 × 0.70 / 500 = 0.98` of its old level.
Revenue breaks even at `500 / 700 ≈ 71.43%` retention, before costs or new demand.

Neither a 30% churn prediction nor a guaranteed 90-day rollout is established by
those calculations. Retain the arithmetic, revise the unconditional revenue claim,
and mark retention/demand as unknown until tested. This example is a protocol
illustration, not a live model transcript or business recommendation.
