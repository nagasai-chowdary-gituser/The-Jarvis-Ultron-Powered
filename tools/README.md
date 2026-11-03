# tools/ — earned recipes

A **recipe** is a saved skill: Python that solved something at the desk, promoted
so next time it is one call (`recipes.run('name', ...)`) instead of regenerating
code. Cheaper (≈3× per AGENTS.md §9) and more reliable, because it is known-good.

**A recipe is a module here that exports `SCHEMA` + `run(**kwargs)`.** Dropping the
file in is the whole install — `recipes.py` auto-discovers it. See `count_words.py`
for the template.

```python
SCHEMA = {"name": "x", "description": "...", "phone_safe": False}
def run(**kwargs) -> dict:
    ...
    return {"ok": True, ...}
```

Rules that `recipes.save()` enforces (§7.1):
- **`phone_safe` defaults to False.** A recipe is opened to the phone/Telegram
  deliberately, never by accident — the reachable surface must not creep.
- **No `eval`/`exec`/`subprocess`/`__import__`/`importlib`.** Promoted, a recipe is
  phone-reachable; one that shells out is a `run_python` bypass. Do the work with
  the `lib/` functions instead.
- Earned from real use, not guessed in advance.
