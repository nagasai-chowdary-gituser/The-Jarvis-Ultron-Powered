"""G33: the per-request tool surface stays small, measured not assumed."""
import json
import _harness as h

h.fresh_db()
import brain

try:
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")
    count = lambda s: len(enc.encode(s))
    exact = True
except Exception:
    count = lambda s: len(s) // 4
    exact = False

schema = json.dumps(brain.TOOLS)
schema_tokens = count(schema)
lib_md = (h.ROOT / "lib" / "LIB.md").read_text(encoding="utf-8")
lib_tokens = count(lib_md)
total = schema_tokens + lib_tokens

h.need(len(brain.TOOLS) <= 6,
       f"{len(brain.TOOLS)} tools; the surface grows with every one")
h.need(schema_tokens < 1200,
       f"tool schemas cost {schema_tokens} tokens per request")
h.need(total < 2500, f"the whole tool surface costs {total} tokens per request")

# the four tools must actually be the four, not renamed stubs
names = {t["name"] for t in brain.TOOLS}
h.need(names == {"run_python", "schedule_job", "recall", "ask_boss"},
       f"unexpected tool set: {sorted(names)}")

# and every tool must declare real parameters, not an empty schema
for t in brain.TOOLS:
    props = (t.get("parameters") or {}).get("properties") or {}
    h.need(props, f"{t['name']} has no parameters at all")
    h.need(t.get("description"), f"{t['name']} has no description")

print(f"# schema={schema_tokens} lib={lib_tokens} total={total} exact={exact}")
h.ok("TOKEN_SURFACE_OK")
