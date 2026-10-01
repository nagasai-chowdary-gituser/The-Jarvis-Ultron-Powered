"""G20: a dependent step waits for its inputs while unrelated work proceeds."""
import time
import _harness as h

h.fresh_db()
import execution

events = []

def research(name):
    def run(_):
        time.sleep(0.4)
        events.append(f"start-{name}")
        return f"data-{name}"
    return run

def compare(inputs):
    events.append("compare")
    h.need(set(inputs) == {"a", "b", "c"},
           f"compare ran without all inputs: {sorted(inputs)}")
    h.need(all(v and v.startswith("data-") for v in inputs.values()),
           f"compare received empty inputs: {inputs}")
    return "comparison:" + ",".join(sorted(inputs.values()))

def write(inputs):
    events.append("write")
    h.need("compare" in inputs and inputs["compare"].startswith("comparison:"),
           f"write ran without the comparison: {inputs}")
    return "written"

steps = [
    execution.Step(id="a", run=research("a")),
    execution.Step(id="b", run=research("b")),
    execution.Step(id="c", run=research("c")),
    execution.Step(id="compare", run=compare, needs=("a", "b", "c")),
    execution.Step(id="write", run=write, needs=("compare",)),
]
r = execution.run_graph(steps)
h.need(all(x["ok"] for x in r["results"].values()),
       f"a step failed: {[(k,v.get('error')) for k,v in r['results'].items() if not v['ok']]}")

h.need(r["results"]["write"]["value"] == "written", "the final step did not run")
h.need(events.index("compare") > 2, f"compare ran too early: {events}")
h.need(events.index("write") > events.index("compare"), f"wrong order: {events}")
h.need(r["peak_parallel"] >= 3,
       f"the three independent lookups did not overlap: {r['peak_parallel']}")
h.need(r["wall_seconds"] < r["serial_seconds"],
       "the graph was no faster than running it all in a line")

# a failed dependency must SKIP its dependants rather than run them blind
def boom(_): raise RuntimeError("the source is down")
r2 = execution.run_graph([
    execution.Step(id="src", run=boom),
    execution.Step(id="uses", run=lambda i: "should not run", needs=("src",)),
    execution.Step(id="other", run=lambda i: "unrelated"),
])
h.need(r2["results"]["src"]["ok"] is False, "a raising step reported success")
h.need(r2["results"]["uses"].get("skipped") is True,
       "a step ran on a failed dependency's missing output")
h.need(r2["results"]["other"]["ok"] is True,
       "an unrelated branch was cancelled by someone else's failure")

# a cycle must be refused, not deadlock
try:
    execution.run_graph([
        execution.Step(id="x", run=lambda i: 1, needs=("y",)),
        execution.Step(id="y", run=lambda i: 1, needs=("x",)),
    ])
    h.fail("a circular graph was accepted")
except execution.CircularDependency:
    pass

h.ok("DEPENDENCIES_OK")
