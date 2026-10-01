# AGENTS.md — Jarvis operating contract

**Read this at the start of every phase.** It is the authority. Where BUILD.md and this file disagree, this file is newer and wins — every divergence is recorded under "Deviations" with its reason.

---

## 1. Mission

A **general** personal agent. Not a fixed menu — the user asks for anything and it figures out how. One brain, three transports, different permissions per transport.

Target: Windows 11. Desk + phone + Telegram.

---

## 2. Core design decision

**Generality comes from code execution, not from a long tool list.**

The primary tool is `execute_python(code, description)`. The agent writes Python on demand and runs it in a subprocess. WhatsApp counts, a PowerPoint deck, checking a process, something nobody anticipated — one path.

**Do not build a catalogue of narrow tools.** That was the old design and it was wrong. The only hand-written tools that may exist are the read-only status tools the phone needs (§6) and recipes earned from real use (§7).

---

## 3. Security boundary: transport, not tiers

The primary gate. Everything else is secondary.

| Capability | Desk | Phone | Telegram |
|---|---|---|---|
| `execute_python` | allowed | **PIN-gated** | **PIN-gated, expiring** |
| Built-in action tools | allowed | **PIN-gated** | **PIN-gated, expiring** |
| Saved recipes (`phone_safe=True`) | allowed | PIN-gated | — |
| Saved recipes (`telegram_safe=True`) | allowed | — | PIN-gated |
| Saved recipes (neither flag) | allowed | **DENIED** | **DENIED** |
| Read-only status | allowed | allowed | allowed |

### 3-CURRENT. What is actually built, and why it differs

**The table above is the original transport-based design. It is not what runs.**
The owner re-specified this deliberately, and the reason is in §3 itself: under
transport gating, every real request from the phone ended in "I can only do that
at the computer", which is the opposite of the point of phoning your own
machine. The same argument applies one level further in — a gate that fires on
*where the request came from* fires on almost everything, and a prompt that
fires on almost everything is one the owner learns to clear without reading.

So the gate now fires on **what the action would do**, not on which door it came
through:

- `safety.risk_of(text)` classifies a request, and returns `None` for ordinary
  work. Deliberately narrow: a classifier that sees danger everywhere rebuilds
  the prompt-on-everything problem by the back door.
- `safety.authorize(action)` raises `Blocked` only for the five entries in
  `HIGH_RISK`: `permanent_delete`, `payment`, `credentials`,
  `external_broadcast`, `system_config`.
- Everything else — opening apps, reading, browsing, messaging, writing inside
  the workspace, running code — **never prompts, on any transport.** That is the
  ninety-nine percent, and it is the whole reason the remaining one percent is
  worth interrupting for.
- `safety.authenticate(pin)` opens one 30-minute trusted session
  (`SESSION_MINUTES`). The PIN is compared with `hmac.compare_digest` and never
  appears in a return value, a log or the database.

Names have changed with it: there is no `session.py` and no `verify_pin()`. The
session lives in `safety.py` as `authenticate` / `trusted` / `seconds_left` /
`end_session`.

**Deletion is still refused at every PIN state** (§4.3a). The PIN unlocks the
machine; it does not unlock deletion. That is unchanged and not negotiable.

---

### 3a. Authorisation is a SESSION, not a question per action

One PIN opens a window; inside it, nothing asks again.

| Channel | Window |
|---|---|
| Desk | until Jarvis is closed |
| Phone | 30 minutes |
| Telegram | 30 minutes |

`session.py` holds one grant per channel, on the **process**, not on a conversation. Not persisted — a restart is a logout, and writing it to disk would make "until you close it" a lie. The PIN itself is never stored, only the fact and the expiry. Five wrong attempts freeze that channel for 15 minutes.

**Why this exists**, from a real transcript: *"reply yes 7492"* → approved → *"carry on"* → *"it still needs a restart"* → *"reply yes 8887"* → forever. Two defects compounding — authorisation was per-action, **and approval did not resume the work**. `tg.Approval` now re-runs the original request once an approval lands, so "Running it now" stopped being false.

**A window is authorisation, not exemption.** Deletion and money have no approval path at any level. That is what makes them rules rather than defaults.

**Enforcement:** one check, at the top of `brain.dispatch_tool()`, before preflight and before any argument parsing. Not a config flag, not a tier lookup, not spread across transports. One line, one place, auditable in a single grep.

**Rationale:** at the desk the user is physically present, so nothing gates it.

**The phone was desk-only and it was wrong.** Under that rule a call had nine read-only tools, and every request for real work ended in "I can only do that at the computer" — which is the opposite of the reason to phone your own machine. The owner asked for it open and it is open, behind two gates: `From` must equal `MY_NUMBER` (checked in `/voice`, before any audio connects) and the caller must clear the PIN. A correct PIN on a call is now full control of this machine.

That makes the PIN a real credential, so `safety.verify_pin()` **refuses to accept a default or trivial PIN on any remote transport** rather than accepting it with a warning. `desk.py` prints a loud banner at startup while `JARVIS_PIN` is weak, because otherwise the symptom is a silent "it still will not do anything".

**Telegram is open too, at the owner's request, and the reason it was shut still stands.** A bot token is one string with no second factor, and it leaks from screenshots, backups and chat logs. So it is opened behind three things rather than one: only `TELEGRAM_CHAT_ID` is answered (silently — replying confirms the bot exists), the PIN is required, and **that unlock expires** (`TELEGRAM_PIN_TTL_S`, 15 minutes; `/lock` ends it immediately).

**The expiry is the part the phone does not need**, and it is what makes this defensible. A call ends when you hang up, so an unlock there is bounded by the call. A Telegram session is created once and lives as long as Jarvis does — without a clock, a token that leaked tomorrow would walk straight into a session unlocked yesterday.

`phone_safe` and `telegram_safe` remain separate flags, so a tool can be opened to one channel without the other.

**Approval is never something the model can say.** On a call the model produces the speech, so "yes" is evidence the model said a word, not that a human agreed; DTMF arrives on its own websocket event and the model cannot emit one. On Telegram the model writes the OUTGOING messages but cannot write an INCOMING one — inbound comes from `getUpdates`, i.e. from whoever holds the chat — so a reply is a human act in exactly the same way.

Both bind approval to a SHA-256 digest of the exact held code (`phone.Keypad`, `tg.Approval`): different code is held again, and one signal authorises one run. The Telegram reply must carry a per-request code, so a bare "yes" typed mid-conversation cannot clear something the user never read.

**Deletion is refused at every PIN state on every transport** (§4.3a). The PIN unlocks the machine; it does not unlock deletion.

---

## 4. What actually constrains generated code — read this carefully

### 4.1 The privilege reality

**On the desk transport, `execute_python` runs with the user's full Windows privileges.** It can read, write, and delete anything the user can.

This is not a defect to be patched later. Real confinement means a container or a deny-ACL service account, and both break the mission: a jailed subprocess cannot drive COM, cannot open PowerPoint, cannot control the machine. The user asked for an agent that operates their system. An agent that operates the system has the user's privileges. Those are the same sentence.

The design response is to make execution **observable and interruptible**, not confined. Four layers, none of which is a sandbox:

1. **Preflight AST scan** (§4.3) — catches the agent misunderstanding the user.
2. **HUD visibility** — every execution's full source appears on screen.
3. **Audit log** — every execution recorded to SQLite.
4. **Kill switch** — HUD button, kills the process tree.

### 4.2 `safe_path()` — its real scope

`safe_path()` resolves a path, jails it to `WORKSPACE`, and rejects traversal and symlink escapes. **It applies to hand-written recipe code only.**

It does **not** and cannot constrain `execute_python`. Generated code calls `open()` directly; nothing routes it through `safe_path()`. BUILD.md described it as a jail on all file writes — under this architecture that is false, and a guarantee believed but absent is worse than none. Stated plainly here so nobody relies on it.

Workspace-as-cwd for the subprocess is a **convenience default, not a boundary.** One absolute path walks straight out of it.

### 4.3 Preflight scan — the actual guardrail

Before any generated code executes, AST-scan it. Flag:

- `shutil.rmtree`, `os.remove`, `os.unlink`, `os.rmdir`, `pathlib.Path.unlink`
- `subprocess.*` / `os.system` / `os.popen` shelling to powershell, cmd, reg, wmic
- file writes resolving outside `WORKSPACE`
- `eval`, `exec`, `compile`, `__import__`, `importlib`, and `getattr(x, "<destructive name>")` indirection
- network POST/PUT/PATCH/DELETE
- reads of `.env`, `.ssh`, `.aws`, browser profile dirs, credential stores

**Clean → runs immediately.** No prompt, no friction. This is the common case and the design depends on it staying frictionless.

**Flagged → holds.** The HUD shows the code with offending lines highlighted and waits for a click. Expected to hit a minority of executions.

This is a **heuristic and must be documented as one.** It catches the realistic failure — the agent misreading an instruction and doing something irreversible. It does not stop a determined adversary who is already executing code as the user.

### 4.3a Deletion is REFUSED, not held — user rule, no exception

**Jarvis cannot delete anything.** Not with permission, not with the user
insisting, not by any route.

**Code built at runtime is refused for the same reason.** `eval`, `exec`,
`compile` and `__import__` are `deletes-files` severity, which means unapprovable.
Found by attacking the audit rather than reading it: `__import__('os').remove(x)`
and `exec("import os; os.remove(x)")` were merely "dynamic", and dynamic was
confirmable — so **one approval ran a deletion the deletion rule refuses
outright**. A rule with an approval-shaped hole in it is not that rule. Nothing
can be promised about code the AST scan cannot see, so refusing it is what makes
every other rule here mean anything. Ordinary `import` is untouched.

Audited adversarially, with confirmation already granted: **13/13 routes refused**
— direct, `getattr`, subscript, shell, PowerShell, `os.system`, and all four
dynamic-execution forms.

This could not be built as a confirmation prompt, because a prompt is precisely
the thing that gets approved. It is a `Verdict.REFUSED` checked in
`execute_python` **after** `user_confirmed`, so no approval path reaches it.

Every route is covered, and each one was found by testing the rule rather than
trusting it:

| Route | Status |
|---|---|
| `os.remove`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, `Path.unlink` | refused |
| `from shutil import rmtree as nuke` | refused |
| `getattr(os, "remove")("x")` | refused — was classified merely "dynamic", which is *confirmable*, so it ran |
| `os.system("del x")`, `"rmdir /s"` | refused |
| `subprocess.run(["cmd","/c","del","x"])` | refused — bare token `del` missed a substring check for `"del "` with a space |
| `Remove-Item`, `rm -rf` | refused |

Moves and renames stay **held, not refused** — they are reversible, and "move it
instead" is the alternative the refusal offers, so refusing those too would leave
nothing to suggest.

The rationale is not distrust of the user. Deletion is the one action with no
undo, and the realistic failure is the agent misreading a sentence, not the user
changing their mind. "Clean up my downloads" should never be one misheard word
from unrecoverable.

### 4.3b Creating files and folders asks first — scoped

Creating anything **outside the workspace** holds for approval. Inside the
workspace it does not.

**`open(path, "w")` counts, and for a long time it did not.** `_CREATION` listed
`os.mkdir` and `shutil.copy` and nothing that actually writes a file, so the rule
never fired for the commonest way there is to create one — verified against the
live preflight, `open('notes.txt','w')` came back CLEAN. `open`/`io.open` with a
write mode, and `Path.write_text`/`write_bytes`, are now creation. `"w"` on an
existing file also **truncates** it, which destroys the contents without ever
calling a delete: the same loss the deletion rule exists to prevent, arriving by
another door.

Relative paths resolve against the workspace **`preflight()` was given**, not the
ambient one — reading the global made the answer depend on an environment
variable rather than the argument passed in, and reported ordinary scratch writes
as escaping the workspace.

That scoping is deliberate. `execute_python` is stateless and writes scratch
files constantly; prompting for each would turn the rule into noise the user
learns to click through, which destroys the protection it was meant to provide.
A path built at runtime cannot be resolved statically, so it holds rather than
being assumed safe.

**The system asks, not the model.** A live test found the opposite: told to ask
before creating, the model asked *verbally* — "please confirm to proceed" — and
stopped. Nothing happened, because it was waiting for permission to ask for
permission. The prompt now states that the hold is automatic and the model must
act.

### 4.4 Confirmation — consent is never a tool argument

Required before: anything the preflight flags, anything that spends money, anything that sends a message or email on the user's behalf. On desk this is a HUD click; on Telegram an inline button; on phone a spoken confirmation plus PIN.

**`confirmed` must never appear in a tool schema.** BUILD.md called for auto-injecting a `confirmed` boolean into every non-green schema. That is a hole, and it was observed live, not theorised: the model set `confirmed: true` on its *first* call and ran `os.remove` with no hold ever shown. The audit table recorded a `confirmed` row and no `held` row — the only visible trace.

Consent arrives **out of band**, through the `user_confirmed=` keyword on `dispatch_tool()`, which only the HUD click path sets. `dispatch_tool` strips any `confirmed` key out of model-supplied arguments unconditionally, before anything reads them.

Guarded by three regression tests in `tests/test_brain.py`:
`test_confirmed_not_advertised_to_the_model`, `test_model_supplied_confirmed_is_ignored`, and `test_hold_writes_a_held_audit_row` — the last one asserts on the audit row, because that absence was the only symptom.

---

## 5. `execute_python` contract

```
execute_python(code: str, description: str) -> dict
```

- **Subprocess always.** Fresh interpreter. Never in-process, never `exec()`.
- cwd = `WORKSPACE`. 60s timeout.
- **Timeout kills the whole process tree**, via `psutil.Process.children(recursive=True)`. Playwright spawns Chromium; killing only the parent orphans the browser and zombies accumulate until reboot.
- Returns `{ok, stdout, stderr, exit_code, duration_s, truncated, preflight}`. Never raises into the caller — every failure is a dict the model can act on.
- **Executions are stateless.** Fresh process each call; variables do not persist. The brain must write intermediate state to files under `WORKSPACE` and re-read it.
- Every execution is logged with its **full source** before it runs.

### Truncation is asymmetric

Both streams cap at 2000 chars, but from opposite ends:

| Stream | Keep | Why |
|---|---|---|
| stdout | **first** 2000 | Output front-loads; headers and early rows matter most. |
| stderr | **last** 2000 | A traceback puts the exception on the final line. Head-truncating keeps import frames and discards the actual error — the model then retries blind and burns a turn. |

### 5.1 A turn ends on a reason, not on a count

**The number of actions must never be why a legitimate task fails.** It was,
and the symptom was a sentence: *"I ran out of steps before finishing that."*
Asked what was next in a queue, the agent had to find the browser, find the
right tab, read it and report — and the twelfth round arrived first.

The defect was not the size of the number. It was that ONE number paid for
everything. A reasoning round, a productive tool call, an observation and a
failed retry each cost exactly one, so three failures were priced the same as
three completed subgoals, and a task going perfectly well could still starve.

`budget.py` is the governor. It counts these separately, because they mean
different things:

| Counted | Meaning |
|---|---|
| rounds | model turns — the loop's own pulse |
| actions | tool calls made |
| progress | signals that something actually changed |
| barren rounds | consecutive rounds with no progress signal — resets on any |
| recoveries | repair attempts, from their own purse, never from the productive one |
| observations | looking without changing anything |

A turn stops only on a condition that is TRUE about the work:

| Verdict | Means | A failure? |
|---|---|---|
| `completed` | the objective is met | no |
| `blocked` | it cannot be done and the reason is known | no — an answer |
| `needs_auth` | a human must authorise it | no — an answer |
| `cancelled` | the owner said stop | no |
| `timeout` | the wall-clock deadline passed | yes |
| `stuck` | `max_barren_rounds` with nothing changing | yes |
| `runaway` | the outer ceiling — 40 rounds or 300 calls | yes, and a bug report |

`blocked` and `needs_auth` are deliberately not failures. "Your Chrome has no
debugging port, so I cannot read that tab" is a correct, finished answer that
happens to require someone else; filing it as a failure would hide the one
thing the owner needs to know.

**Budgets are set from the request, not fixed.** `for_request()` has three
tiers: a short question with no heavy words gets 60 s and 12 rounds, anything
that reads like real work (`research`, `compare`, `until`, `then`, `debug`,
`queue`, … or over 25 words) gets 420 s and 60, and everything between gets
300 s and 40. These are ceilings, not costs — a question that finishes in one
round pays for one round. The point is that a long job is no longer measured
against a short one's allowance.

**Loops are detected by outcome, not by repetition.** The fingerprint is tool
+ arguments + *result*. The same call returning something different each time
is polling, which is legitimate; the same call returning the same thing is a
loop, and strategy changes.

---

---

## 6. Read-only status tools

The only hand-written tools permitted. Available on every transport, no PIN:

- `project_status` — running dev processes, `git log -1`, uncommitted count, newest file
- `recipe_list` — names + descriptions of saved recipes
- `verify_pin` — unlocks non-read-only actions for the session
- `answer_question` — fallback when nothing else matches

---

## 6.1 Vision — Jarvis can see the screen

**The capability that separates an agent from a code runner.** Without it Jarvis
could open Gmail and then had to say *"let me know when you're signed in"*, because
opening a window and reading one are different things and it could only do the
first. Observed live, and it is what made the whole thing feel like an MCP executor.

Two tools, in `vision.py`:

- `see_screen(question, window?)` — screenshot → downscale → vision model → one
  spoken sentence. Reads real text: unread counts, senders, subject lines, error
  dialogs, whether a download finished.
- `list_windows()` — open window titles. Cheaper than a screenshot, and the title
  often already holds the answer (`"(3) WhatsApp"` means three unread).

**Window capture must use `PrintWindow`, never `ImageGrab.grab(bbox=rect)`.** The
latter screenshots the screen *region*, so anything overlapping the target gets
captured instead — asking about WhatsApp returned a reading of the Chrome window
on top of it. `PW_RENDERFULLCONTENT` (flag 3) is required or Chrome and Electron
apps return black. Fallbacks are raise-then-grab, then full screen, and `source`
always says which one was used so the model never reports on a window it did not
actually see.

**These are RECIPE kind, not READONLY.** A screenshot carries whatever is visible
off the machine — open documents, a password manager, someone else's message. It
is read-only in the safety sense but must never be reachable from a spoofed phone
call, so the transport gate denies it on phone and Telegram.

The system prompt carries an explicit **LOOK, DON'T ASK** rule: after opening
anything, read the result rather than asking the user what they see.

---

## 7. Recipes

When `execute_python` solves something at the desk and it works, offer to save it to `tools/` as a named recipe. Next time it is one call instead of regenerating code — cheaper and faster. **Earned from real use, not guessed in advance.**

Auto-discovery: any module in `tools/` exporting `SCHEMA` + `run()` registers on import. Dropping a file is the whole install process.

### 7.1 Promotion is the real privilege boundary

A recipe is arbitrary Python that has been given a name. Promoting it does not sanitize it. Two consequences, both enforced:

**`phone_safe: bool = False` in every `SCHEMA`, default False.** Promotion asks two separate questions: *promote this?* then *callable from a phone?* Most recipes never need the second. Without this, the phone's reachable surface becomes the union of every recipe ever promoted — a set that only grows, reviewed ever more casually.

**Reject at promotion** any recipe containing `eval`, `exec`, `subprocess`, `__import__`, or `importlib` unless explicitly overridden. One promoted recipe that shells out is a complete `execute_python` bypass reachable by phone — exactly what §3 exists to prevent.

### 7.2 Promotion parameterizes

Saving literal working code yields a tool that does exactly one thing. Promotion runs a second generation pass: refactor into `run(**kwargs)` + `SCHEMA` with real parameters, keeping the original source as a comment for provenance.

### 7.3 Recipe lookup is semantic

"Check my project" and "what's my repo status" must hit the same recipe. The routing model selects from recipe descriptions; exact-name matching is insufficient.

---

## 8. Stack

Python **3.11**, FastAPI, uvicorn. Windows target.

100% OpenAI for all reasoning, transcription and synthesis.

**One carve-out:** a local keyword spotter (openWakeWord, ~1MB, CPU) for wake-word detection only — *keyword spotting only; all reasoning, transcription and synthesis remain OpenAI.* Streaming the mic to Whisper 24/7 would cost ~$260/month and ship the room's audio all day; that is why the exception exists.

All model IDs and rates live in `models.py`. One file — a rename is a one-line change. Nothing anywhere else hardcodes a model name.

---

## 9. Cost contract

- System prompt + tool schemas go **first, byte-identical, every request**, so the prefix caches. Cached input bills at ~10% of standard.
- **Every** tool return truncated to 2000 chars before entering the message list (§5, asymmetric). Untrimmed output compounding across a 15-turn loop is where agent costs explode.
- `server_vad` on the Realtime session — no paying for silence on an open line.
- **Every loop is bounded, but never by a count of actions.** `MAX_CALLS_PER_SESSION = 15` was the original rule and it was wrong in a way that cost real work: it spent the same allowance on a completed subgoal as on a failed retry, so a legitimate multi-step request died mid-flight with "I ran out of steps" - a sentence that describes the counter, not the task. A budget now ends a turn on a TRUE terminal condition (§5.1): finished, blocked, cancelled, out of time, making no progress, or running away. `RUNAWAY_TOOL_CALLS = 300` and `runaway_rounds = 40` remain as the outermost safety ceiling - reaching either is a bug report, not a normal completion.
- **Desk defaults to Realtime, not the chained path.** This reverses the original rule, on measurement — see §9.3.
- Telegram routes through the cheap model first to classify command vs chatter before spending on the main brain.
- Log tokens and cost per call to SQLite.
- `DAILY_CAP_USD` kill switch: past it, refuse all non-read-only actions; the phone handler says the budget is spent.
- **The recorded figures must be readable back.** `cost.summary(period)` is the one query and the one formatter; `api_usage` (READONLY, no PIN, every transport), `/usage` in Telegram and `python cost.py [period]` all read the same words off it. Recording without reporting was the state for six phases: the numbers were right and nothing could reach them, so asked for today's usage the agent estimated — the exact invented number §1 forbids, and the most convincing kind, because a spend figure sounds authoritative. `summary()` returns `spoken` for voice and `report` preformatted for sending, so a model relaying it to Telegram passes the string through instead of retyping digits.

### 9.1 Measured economics (verified rates, 2026-09-15)

Modelled against real pricing, not assumptions. Numbers per interaction:

| Component | Cost | Share |
|---|---|---|
| TTS (`tts-1`, 300 chars) | $0.00450 | **77%** |
| STT (`gpt-4o-mini-transcribe`, 15s) | $0.00075 | 13% |
| BRAIN (`gpt-5.6-luna`, warm cache) | $0.00061 | 10% |
| **Desk interaction total** | **$0.00586** | ~170 per $1 |

**The counter-intuitive result: speech output dominates, not thinking.** `tts-1`
bills $15 per 1M characters, so a 300-character reply costs 7x the entire brain
turn that produced it.

**Therefore — spoken replies are one or two short sentences.** This is a hard rule
in the system prompt, not a style preference. Cutting replies from 300 to 120
characters nearly halves the cost of every desk interaction. Detail goes on the
HUD, which is free; speech stays terse.

Other measured levers:

- **`gpt-5.6-luna` as BRAIN, escalate to `terra` only on repeated failure.** terra
  costs 10x. At a 20% luna failure rate the blended cost is $0.00195 vs $0.00610
  for terra-always — **68% saved**. Even at a 50% failure rate it still saves 35%.
- **Prefix caching pays double on luna**: a 10x cache discount ($0.20 -> $0.02)
  versus 2x on `gpt-4o-mini`. Measured 51% saving per warm turn. This is why the
  byte-identical-prefix rule is load-bearing and not mere tidiness.
- **`gpt-realtime-mini` over `gpt-realtime`**: 3.2x cheaper audio. A 3-minute call
  is ~$0.019 instead of ~$0.067.
- **Recipes are 3x cheaper than regenerating code**, every call after the first.
- `gpt-4o-mini` is marginally cheaper than luna warm ($0.00055 vs $0.00061) but is
  a far weaker model; six hundred-thousandths of a dollar does not justify worse
  code generation, since a failed execution costs a full retry.

**Projected monthly at heavy use** (30 desk interactions/day + 2 calls/week):
**~$5.43** — $5.27 desk, $0.15 phone. Desk dominates purely by volume.

Realtime audio token rate (~600 tokens/min) is an **estimate**; Phase 7 telemetry
replaces it with measured values.

---

### 9.3 Latency — why desk now defaults to Realtime

BUILD.md said the chained path is ~4x cheaper and Realtime is for the phone only.
That was reasoned from `gpt-4o-mini-tts` pricing. **That model is not on this key**,
so the only TTS available is `tts-1` at $15 per 1M characters, and the conclusion
does not survive measurement.

Measured, end to end:

| | Time to first audio | Cost per exchange |
|---|---|---|
| Chained (STT → luna → tts-1) | **~11 s** | $0.0012 |
| Realtime GA (`gpt-realtime-mini`) | **0.83 s** | $0.0025 |

Streaming cannot rescue the chained path. Measured time-to-first-byte:

- `tts-1`: **~1.3–1.9 s regardless of input length** — 4 characters costs the same
  wait as 214. It is fixed overhead, so sentence-chunking does not move the floor.
- LLM streaming: first token at 0.92 s vs 2.51 s complete. Real win.
- STT streaming: 1.60 s vs 1.64 s. No win at all.

So the best a fully-pipelined chained path could reach is ~4.5 s, still five times
slower than Realtime. **Realtime is 13x faster for 2.2x the cost.** At 30
interactions a day that is $2.25/month against $0.99 — both trivial, and latency
is what makes the difference between a tool and a toy.

`python desk.py` is Realtime. `python desk.py --chained` is the cheap path.

Audio token rate is **20 tokens per second of speech**, measured — not the ~10
estimated earlier. `cost.Usage` therefore counts audio tokens separately from
text: folding them together understates a realtime call by roughly 10x and would
silently break `DAILY_CAP_USD`.

## 9.2 OpenAI API facts — verified by live call, do not rewrite from memory

Every item here was discovered by testing against the real key on 2026-09-15.
Several would have broken Phase 3 silently.

- **Use `/v1/responses`, not `/v1/chat/completions`.** luna rejects function tools
  on chat.completions outright: *"Function tools with reasoning_effort are not
  supported ... use /v1/responses or set reasoning_effort to 'none'."*
- **SDK must be `openai>=3.x`.** 1.58.1 has no `client.responses` at all.
- **`temperature` is rejected** — only the default (1) is supported.
- **`max_tokens` is rejected** — the parameter is `max_output_tokens` on
  Responses (`max_completion_tokens` on chat.completions).
- **Echo output items back verbatim, minus `status`.** Feeding a `model_dump()`
  straight back fails with *"Unknown parameter: input[1].status"*. Strip it.
- **Reasoning items must be echoed back too.** They carry `encrypted_content`
  that preserves reasoning continuity across tool calls. Dropping them loses it.
- **Tool results** go back as
  `{"type": "function_call_output", "call_id": ..., "output": "<json string>"}`.
- **Prefix caching is near-total and automatic** — but only above a threshold.
  **The cached prefix must exceed 1024 tokens or caching never engages at all.**
  Measured precisely: at 1023 input tokens, `cached_tokens=0` on every call; at
  1120 tokens, `cached_tokens=1117` (99.7%) from the second call onward. Cost per
  turn fell from $0.000236 to $0.000035 — **6.7x cheaper for 97 extra tokens.**

  **Therefore `SYSTEM_PROMPT` in agent.py must never be shortened below ~1100
  tokens.** This inverts the usual instinct: here a shorter prompt is *more*
  expensive, because it forfeits luna's 10x cache discount on exactly the short
  turns that make up most desk use. The length is load-bearing. Anyone trimming
  it for tidiness will silently multiply the running cost by about seven.
- **Reasoning is nearly free on luna.** Measured on an identical task:
  `none`=122 output tokens, `low`=181, `medium`=203. The gap from none to medium
  is ~$0.0001 per turn — a fiftieth of one TTS reply. Since a failed execution
  costs a whole retry, **buy the reasoning**.
- **The model replies in Markdown by default.** TTS reads `**bold**` aloud as
  asterisks, so the system prompt must forbid markup explicitly.

### Escalation ladder (revised on the above)

Reasoning is cheap and terra is not, so raise thinking before changing model:

| Rung | Model | Reasoning | When |
|---|---|---|---|
| 1 | `gpt-5.6-luna` | `low` | default |
| 2 | `gpt-5.6-luna` | `high` | after 1 failed execution — costs ~$0.0001 more |
| 3 | `gpt-5.6-terra` | `low` | after 2 — 10x price, genuinely last resort |

---

## 10. Realtime audio — do not get creative

**Twilio Media Streams sends 8kHz mu-law base64. The OpenAI Realtime API accepts `g711_ulaw` natively. Set both sides to `g711_ulaw` and relay the base64 payloads unchanged.**

Do not resample. Do not use ffmpeg. Do not convert to PCM.
**If you are writing audio conversion code, you have made a mistake.**

### 10.1 GA Realtime facts — verified by live connection, 2026-09-15

The instruction to fetch the docs paid off immediately. Every item below would
otherwise have broken Phase 4 silently:

- **The `OpenAI-Beta: realtime=v1` header is DEAD.** Sending it returns
  `beta_api_shape_disabled`: *"The Realtime Beta API is no longer supported.
  Please use /v1/realtime for the GA API."* Send **only** `Authorization`.
- **The session schema is nested, not flat:**
  ```
  session.audio.input.format         = {"type": "audio/pcm", "rate": 24000}
  session.audio.input.turn_detection = {"type": "server_vad", ...}
  session.audio.output.voice         = "cedar"
  session.output_modalities          = ["audio"]
  ```
  **not** `input_audio_format: "pcm16"`. For Twilio use `{"type": "audio/pcmu"}`.
- Audio events are `response.output_audio.delta` and
  `response.output_audio_transcript.delta` — not `response.audio.delta`.
- `websockets` 13.1 takes `extra_headers`, not `additional_headers`.
- `response.function_call_arguments.done` and `response.done` both report the same
  call. Dispatch must de-duplicate on `call_id` or every tool runs twice.

**Dead-air fix — but NOT with a second `response.create`.** A slow tool means
silence, which reads as a dropped call. The obvious fix, firing a "one moment"
response while the tool runs, is wrong: it returns
`conversation_already_has_active_response`, and because that filler carries the
same tool list, **the model calls the tool a second time.** Observed live —
`psutil.cpu_percent` ran twice for one question, at 2.6x the cost.

Instead, instruct the model in its session prompt to say what it is about to do
*inside the same response*, before making the call.

---

## 11. Deviations from BUILD.md

| BUILD.md | Actual | Why |
|---|---|---|
| Python 3.12 | **3.11** | 3.12 is not installed on this machine; 3.13/3.11/3.10 are. The audio stack (openwakeword, onnxruntime, webrtcvad, sounddevice) has widest Windows wheel coverage on 3.11. Verified: all 15 deps install clean. 3.12 buys nothing here. |
| "`safe_path()` jail on all file writes" | Scope narrowed to recipe code (§4.2) | Cannot constrain generated code. Stating it as universal is a false guarantee. |
| "Confirmation before anything destructive" | Implemented as preflight AST scan (§4.3) | The original had no mechanism deciding what "destructive" means. |
| Recipes are the phone's vocabulary | `phone_safe=False` default (§7.1) | Otherwise the transport gate erodes silently as recipes accumulate. |
| Phase 0 "fetch current pricing" | Pricing entered manually into `models.py` | `GET /v1/models` returns `id`/`object`/`created`/`owned_by` only — **no rates**. IDs verify automatically; rates must come from the pricing page. |
| `MAX_CALLS_PER_SESSION = 15` caps every loop | Adaptive budget; counts are the ceiling, not the rule (§9) | A fixed count charged a failed retry and a completed subgoal the same price, so the engine ran out of allowance on work that was going fine. Observed live: "tell me what is the next song in queue" returned "I ran out of steps before finishing that." Termination now names a real reason. |
| Transport decides what needs a PIN (§3 table) | **Risk decides**; transport decides nothing (§3-CURRENT) | Owner's instruction, and the same reason §3 already gives for opening the phone: a gate keyed to the door fires on everything, and a prompt that fires on everything gets cleared unread. Five named high-risk actions ask; ordinary work never does. Deletion is still refused outright at every PIN state. |
| No phase built recipe promotion | Added as **Phase 5**, Telegram → 6, cost → 7 | Recipes were specified but nothing created them; `tools/` would stay empty forever and the phone would have no vocabulary. |
| Outlook automation | Unavailable | Outlook is not installed. PowerPoint, Excel, Word COM verified registered. |

---

## 12. Phase plan (as revised)

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Scaffold, env, `models.py`, this file | User approves `AGENTS.md` + `models.py` |
| 1 | `safety.py` — transport gate, preflight, `safe_path()`, `Session`, audit log | `pytest` fully green |
| 2 | `brain.py` — dispatcher, `execute_python`, recipe loading, COM worker thread | Desk runs code; phone refused. Both proven. |
| 3 | Desk mode + HUD (`localhost:5051`) | Wake word → arbitrary request → code visible on screen → runs |
| 4 | Phone (Twilio ↔ Realtime) + `DEPLOY.md` | `/health` over tunnel, then live test call |
| 5 | Recipe promotion (`head.py --review`) | A real execution promoted to a parameterized recipe |
| 6 | Telegram transport | Same command works on all three transports |
| 7 | Cost telemetry + kill switch | `python cost.py report` |

---

## 13. Rules

- Read this file at the start of every phase.
- **Stop at every gate.** Report pass/fail honestly. A failing test reported as passing costs a day.
- Real keys live in `.env` only — never in code, never in chat, never in logs.
- If an approach here seems wrong, say so **before** building it.
- If dispatch logic gets written twice, stop and extract the shared part.
