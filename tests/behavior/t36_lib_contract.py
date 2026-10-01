"""G36: everything LIB.md advertises exists and actually runs.

The index is what the model is told it can call. A function listed there and
missing here is a tool call that fails at runtime for no reason the model can
understand or recover from."""
import inspect, re
import _harness as h

h.fresh_db()
import importlib

lib_md = (h.ROOT / "lib" / "LIB.md").read_text(encoding="utf-8")
calls = re.findall(r"^(\w+)\.(\w+)\(", lib_md, re.M)
h.need(len(calls) >= 20, f"only {len(calls)} calls documented; index too thin")

# Which modules are imported bare rather than from the lib package. This list
# has to track LIB.md: adding tasks and verify to the index without adding them
# here made the check look for `lib.tasks`, which does not exist. The gate
# caught it, which is the point of checking the index against reality.
TOP_LEVEL = {"state", "browser", "tasks", "verify"}
checked = 0
for module_name, func in sorted(set(calls)):
    mod = (importlib.import_module(module_name) if module_name in TOP_LEVEL
           else importlib.import_module(f"lib.{module_name}"))
    h.need(hasattr(mod, func),
           f"LIB.md advertises {module_name}.{func}() which does not exist")
    h.need(callable(getattr(mod, func)),
           f"{module_name}.{func} is documented as a call but is not callable")
    checked += 1

h.need(checked >= 20, f"only {checked} calls verified")

# and the ones that are safe to invoke must genuinely work
from lib import files, desktop, web
import state as st

s = desktop.stats()
for key in ("os", "cpu_percent", "memory_percent", "disk_free_gb", "cpu_count"):
    h.need(key in s, f"desktop.stats() is missing {key}")
h.need(0 <= s["cpu_percent"] <= 100, f"impossible cpu reading: {s}")
h.need(s["disk_free_gb"] > 0, f"impossible disk reading: {s}")
h.need("Windows" in s["os"], f"unexpected os: {s['os']}")

apps = desktop.installed_apps()
h.need(len(apps) > 10, f"only {len(apps)} apps discoverable")

ws = h.workspace()
files.write(f"{ws.name}/contract.txt", "contract")
h.need(files.exists(f"{ws.name}/contract.txt"), "files.exists disagrees with write")
h.need(files.read(f"{ws.name}/contract.txt") == "contract", "read/write disagree")
h.need(any(x["name"] == "contract.txt" for x in files.ls(ws.name)),
       "files.ls does not see a file that exists")

st.set_fact("contract_check", "yes")
h.need(st.facts().get("contract_check") == "yes", "set_fact/facts disagree")

# the documented NOT-possible call must still refuse rather than silently work
from lib import whatsapp
try:
    whatsapp.call("anyone")
    h.fail("whatsapp.call succeeded; LIB.md says it cannot")
except NotImplementedError:
    pass

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("LIB_CONTRACT_OK")
