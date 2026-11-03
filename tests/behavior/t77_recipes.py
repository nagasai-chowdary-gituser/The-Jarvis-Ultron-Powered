"""G77: recipes are real - earned skills Jarvis can save and call, safely.

tools/ was empty, so every repeated task started from zero. This proves the
recipe system: the example recipe is discovered and runs, lookup is by
description (§7.3), promotion writes a new recipe, and the §7.1 boundary holds -
a recipe that would shell out is REFUSED, because promoted it becomes
phone-reachable.
"""
import shutil
import tempfile
from pathlib import Path

import _harness as h

h.fresh_db()
import safety
import recipes

# 1. The example recipe is auto-discovered.
names = [r["name"] for r in recipes.list()]
h.need("count_words" in names, f"count_words was not discovered: {names}")

# 2. It runs on a real workspace file.
ws = h.workspace()
sample = ws / "sample.txt"
sample.write_text("one two three\nfour five\n", encoding="utf-8")
rel = str(sample.relative_to(safety.WORKSPACE))
r = recipes.run("count_words", path=rel)
h.need(r.get("ok") and r.get("words") == 5,
       f"the recipe did not run correctly: {r}")

# 3. Lookup is by description, not exact name (§7.3).
hits = [x["name"] for x in recipes.find("count the words in a text file")]
h.need("count_words" in hits, f"find() did not match by description: {hits}")

# 4. The prompt index lists it.
h.need("count_words" in recipes.index(), "the recipe index omits the recipe")

# 5. save + the §7.1 rejection, in a temp dir so the repo's tools/ is untouched.
tmp = Path(tempfile.mkdtemp(prefix="recipes-check-"))
real_dir = recipes.TOOLS_DIR
recipes.TOOLS_DIR = tmp
recipes._index_cache["sig"] = None
try:
    good = recipes.save("double", "double a number",
                        "def run(n, **k):\n    return {'ok': True, 'out': n * 2}\n")
    h.need(good.get("ok"), f"a clean recipe failed to save: {good}")
    h.need(recipes.run("double", n=21).get("out") == 42,
           "the saved recipe did not run")

    # A recipe that shells out is refused - promoted, it is a phone bypass.
    bad = recipes.save("sneaky", "x",
                       "import subprocess\ndef run(**k):\n    return subprocess.run(['x'])\n")
    h.need(bad.get("refused") and not bad.get("ok"),
           f"a shelling recipe was NOT refused: {bad}")

    # Every dynamic-execution name is rejected too.
    for danger, code in (
            ("eval", "def run(**k):\n    return eval('1')\n"),
            ("exec", "def run(**k):\n    exec('x=1')\n"),
            ("__import__", "def run(**k):\n    return __import__('os')\n"),
            ("importlib", "import importlib\ndef run(**k):\n    return 1\n")):
        b = recipes.save("b", "x", code)
        h.need(b.get("refused"), f"{danger} was not rejected at promotion: {b}")

    # Code with no run() is rejected.
    h.need(not recipes.save("nope", "x", "print(1)\n").get("ok"),
           "a recipe with no run() was accepted")
finally:
    recipes.TOOLS_DIR = real_dir
    recipes._index_cache["sig"] = None
    shutil.rmtree(tmp, ignore_errors=True)   # test-harness cleanup, not the agent

h.ok("RECIPES_OK")
