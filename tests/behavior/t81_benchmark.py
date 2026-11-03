"""G81: the benchmark produces a REAL, deterministic score and pollutes nothing.

"How good are you" is now answered by a count, not an estimate. This proves the
harness itself: it runs a graded suite, the capability tier scores high, the
result is deterministic across runs, and - critically - running it does not write
into the owner's real memory or reliability data unless --record is asked for.
"""
import _harness as h

h.fresh_db()
import benchmark
import state

before_db = state.DB_PATH

r1 = benchmark.run_benchmark()
h.need(r1["rate"] is not None, "the benchmark produced no score")
h.need(r1["attempted"] >= 18, f"too few tasks attempted: {r1['attempted']}")
h.need(r1["by_tier"].get("capability", {}).get("rate", 0) >= 0.9,
       f"the capability substrate is weak: {r1['by_tier']}")

# Deterministic: an isolated DB means the same answer every run.
r2 = benchmark.run_benchmark()
h.need(r1["rate"] == r2["rate"],
       f"the benchmark is not deterministic: {r1['rate']} vs {r2['rate']}")

# Isolation 1: the owner's DB_PATH is restored after the run.
h.need(state.DB_PATH == before_db, "the benchmark left the DB redirected")


# Isolation 2: without --record it writes no benchmark rows into the real DB.
def _bench_rows():
    try:
        return len(list(state._conn().execute(
            "SELECT 1 FROM outcomes WHERE channel='benchmark'").fetchall()))
    except Exception:
        return 0


h.need(_bench_rows() == 0,
       "the benchmark polluted the real outcomes without --record")

# Every result is a grounded pass/fail/skip, never an unlabelled guess.
for res in r1["results"]:
    h.need(res["status"] in ("pass", "fail", "skipped"),
           f"a task had no real verdict: {res}")

# --record DOES log the summary, deliberately.
benchmark.run_benchmark(record=True)
h.need(_bench_rows() > 0, "--record did not log the benchmark outcomes")

h.ok("BENCHMARK_OK")
