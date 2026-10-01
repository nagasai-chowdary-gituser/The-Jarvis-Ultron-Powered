# DELETE.md — Phase 0 inventory and delete list

Produced under REBUILD.md Phase 0. **No code has been written or deleted.**
This is the gate. Read it, then approve, amend, or reject.

---

## 0. BLOCKER — read this first

REBUILD.md Phase 1 says: *"Nothing is commented out or kept 'just in case' —
delete it. Git has it."*

**Git does not have it.**

```
$ git log --oneline
fatal: your current branch 'master' does not have any commits yet
$ git ls-files | wc -l
0
```

Zero commits. Zero tracked files. Every line in this project exists in exactly
one place: the working directory. Deleting anything today is **permanent**.

There is also a standing instruction from you, given early and never withdrawn:
*"add the strict rule that do not delet any filkes by agent even i give
permission also."*

**One commit removes both problems.** `git add -A && git commit -m "before
rebuild"` costs five seconds and makes every deletion below reversible with one
command. I am not deleting anything until that exists.

---

## 1. The inventory

| | lines |
|---|---|
| Application `.py` | **16,685** across 30 files |
| Tests | **9,440** |
| **Total** | **26,125** |

Largest files:

```
1906 brain.py      1795 desk.py     1203 safety.py    953 hud_gl.py
 910 browser.py     809 realtime.py  782 watch.py     677 phone.py
 586 vision.py      566 hud_native.py 551 tg.py       529 agent.py
```

## 2. GUI automation — the grep

| term | hits | where |
|---|---|---|
| `pyautogui` | 20 | apps, vision, brain, safety, verify, desk, agent, selftest |
| `screenshot` | 24 | vision, desk, browser, brain, safety, tg, realtime, verify, recovery |
| `click(` | 9 | vision, brain, browser, hud_gl, desk |
| `SetForegroundWindow` | 7 | apps, vision, hud_gl |
| `focus` | 35 | apps (11), brain (9), selftest, browser, vision, agent, verify |
| `settle` | 8 | brain (4), vision, browser, memory |
| `pygetwindow` / `pywinauto` / `uiautomation` / `wait_for_ui` / `locateOnScreen` | 0 | — |

**99 hits.** Every one of the failures in your screenshots traces to this column.

## 3. Tool schemas — 39 tools, **5,648 tokens on every single request**

REBUILD.md budget: **800**. We are **7× over**.

```
 579 watch_add      321 ask_app       267 watch_rule     219 remind_me
 217 click_on       206 press_keys    201 remember       197 see_screen
 197 call_me        196 type_text     191 api_usage      188 execute_python
 179 promise        174 open_app      163 wait_for_screen
 ... 24 more
```

This is the over-tooled problem, measured. Nine of those tools are ones I added
in the last few days — `ask_app`, `wait_for_screen`, `remind_me`, `call_me`,
`promise`, `promises`, `promise_done`. Each was written to fix a symptom, and
each one made tool selection worse for every request that followed.

---

## 4. DELETE — GUI automation (Rule 1)

| What | Lines | Why |
|---|---|---|
| `vision.py` — `grab`, `encode`, `look`, `locate`, `click`, `capture_window`, `_foreground_then_grab`, `wait_until_still` | ~520 of 586 | Screenshot → vision model → pixel click. 5.3s per call, ~1,500 tokens per step, and it targets by position so it can hit the wrong thing. |
| `apps.py` — `focus_window`, `type_text`, `press_keys`, `wait_for_window` | ~220 of 402 | `SetForegroundWindow` + pyautogui. This is "Antigravity's Claude input will not accept focus". |
| `brain.py` — `_see_screen`, `_click_on`, `_type_text`, `_press_keys`, `_ask_app`, `_wait_for_screen` | ~260 | Tool wrappers over the above. `_ask_app` is mine, from two hours ago, and it is the same mistake with a nicer interface. |
| `verify.py` — `_verify_input`, `_verify_app` | ~70 | Verifying pixels by looking at more pixels. |
| `recovery.py` — `window_missing`, `selector_stale` rules | ~20 | Remedies for GUI failures that will no longer exist. |
| `skills/whatsapp-message/` | whole | Sends via `whatsapp://` URI + an Enter keypress. Replaced by the bridge. |

**Keep `browser.py` (910).** Playwright-over-CDP is a **protocol**, not pixels —
it addresses DOM nodes, not screen positions. Rule 1 permits "an API, an HTTP
call, or a library call". Deleting it would remove the one capability that
already works the right way (20/20 on `browsertest.py`, EUR/USD read live).

**Keep `apps.launch` / `open_path` (~60 lines).** `explorer shell:AppsFolder`
is an OS call, not a click.

## 5. DELETE — the PIN system (your point 2)

| What | Lines | Why |
|---|---|---|
| `session.py` | 237 | Whole file. Per-channel PIN windows, freeze counters, attempt tracking. |
| `safety.py` — `verify_pin`, `_verify_pin_legacy`, `pin_is_weak`, `WEAK_PINS`, all `unlocked()` gates | ~180 | You have asked for this three times. |
| `phone.py` — `Keypad` PIN half | ~60 | DTMF PIN entry. The approval half stays. |

**What replaces it:** REBUILD.md §8 — `run_python` is **desk transport only**,
denied at the dispatcher for phone and Telegram. That is a stronger boundary
than a PIN, because a PIN can be shoulder-surfed and a transport cannot.

## 6. DELETE — files that fold into the seven

| What | Lines | Folds into |
|---|---|---|
| `memory.py` | 416 | `state.py` |
| `commitments.py` | 280 | `state.py` |
| `tasks.py` | 273 | `state.py` |
| `cost.py` | 341 | `state.py` |
| `watch.py` | 782 | `worker.py` |
| `agent.py` | 529 | `brain.py` |
| `planner.py` | 156 | `brain.py` |
| `router.py` | 167 | `brain.py` — mine, from an hour ago |
| `recovery.py` | 200 | `brain.py` (what survives of it) |
| `skills.py` | 308 | `lib/LIB.md` — this IS the pattern, done with tokens instead of files |
| `untrusted.py` | 78 | `safety.py` |
| `notify.py` | 428 | `lib/` |
| `tg.py` | 551 | `main.py` |
| `micgate.py` | 172 | dies with the desk mic |

## 7. DELETE — dead and test scaffolding

| What | Lines | Why |
|---|---|---|
| `hud_native.py` | 566 | Dead for weeks. Nothing imports it. |
| `selftest.py` | 289 | Tests keyboard/screen capabilities that are being deleted. |
| Large parts of `tests/` | ~4,000 of 9,440 | `test_verify`, `test_apps`, parts of `test_brain`, `test_voice` test deleted behaviour. |

---

## 8. THREE DECISIONS THAT ARE YOURS, NOT MINE

REBUILD.md's seven-file target has consequences it does not spell out. I am not
deleting these without you saying so.

### (a) The desk interface — 2,993 lines

`desk.py` (1,795) + `hud_gl.py` (953) + `hud.py` (245). The seven-file
architecture has **no HUD**: `main.py` is "Telegram + phone entry points".

Deleting it means **no fullscreen ULTRON screen at all**. You spent a lot of
this project on that interface. It is also not GUI *automation* — it is your own
window, not something it clicks at.

**My recommendation: keep it, count it as an eighth file.** It is the thing you
look at.

### (b) Voice — `realtime.py` (809) + the phone half

REBUILD.md never mentions speech. Phone calls need `realtime.py`. Deleting it
means **Jarvis loses its voice** — no calls, no speaking, text only.

**My recommendation: keep it.** It is the fastest, most-working part of the
system (0.76s to first word, and the call pipeline is now solid).

### (c) WhatsApp bridge — a real ban risk, stated plainly

Phase 2 wants `whatsmeow` / `devmsh/whatsapp-bridge`. It is an **unofficial
protocol client**. It violates WhatsApp's Terms of Service, and **numbers do get
banned** — typically 2–8 weeks after detection trips.

Your own `whatsapp-message` skill says the same thing in its own words:
*"do not reach for whatsapp-web.js or Baileys — those violate WhatsApp's terms
and get personal numbers banned."*

**Use a second SIM.** Replying inside existing chats is far lower risk than
first-contact messages to strangers. If your main number is the one in those
screenshots, I would not point the bridge at it.

---

## 9. The numbers

| | lines | share |
|---|---|---|
| Deleted outright (GUI, PIN, dead, folded) | **~9,900** | |
| Test code removed with it | **~4,000** | |
| **Total deleted** | **~13,900** | **53%** |
| Rewritten smaller (brain, safety, state, worker) | ~4,500 → ~1,800 | |
| Kept as-is (browser, realtime, tunnel, models, HUD*) | ~5,300 | |

**53% deleted** — inside REBUILD.md's 40–70% target.

Tool schemas: **5,648 → ~700 tokens** (39 tools → 4). That is an **87.6%**
reduction on every request.

---

## 10. What I am NOT claiming

- I have not measured that "send hi" drops to under 5s. That needs the bridge
  running, which is Phase 2. I will paste the real `time` output when it exists.
- The four acceptance tests have not been run. They cannot be until Phases 2–5
  are built.
- "I'm ChatGPT" is not in a file I can delete. It happened because there is no
  WhatsApp *reply* path — the model was free-typing into a GUI with no identity
  anchor. The bridge plus a fixed persona in the system prompt is the fix, and I
  will not claim it is fixed until you see it reply correctly.

---

## 11. Gate

**I need three things from you:**

1. **`git add -A && git commit -m "before rebuild"`** — or tell me to run it.
   Nothing gets deleted before this exists.
2. **Decisions (a), (b), (c)** in section 8 — keep the HUD? keep voice? which
   number for the bridge?
3. **Approve this list**, or strike anything you want kept.

Then I execute Phase 1 and the grep in section 2 returns zero.
