"""G82: the greeting reads the real clock (Indian time), and reports YESTERDAY's bill.

Two live complaints: the brief said "Morning, Boss" at every hour, and it showed
today's API cost - which is zero on the first open of a day, so it said nothing.
Fixed: the greeting is morning/afternoon/evening by the real IST hour (a NIGHT
message greets as "evening", the owner's rule), and the bill is yesterday's total.
"""
import time
from datetime import datetime, timezone

import _harness as h

h.fresh_db()
import greet
import realtime
import state
import worker


def _tod(hour):
    return greet.time_of_day(datetime(2026, 1, 1, hour, 0, tzinfo=timezone.utc))


# 1. The three buckets, including the owner's rule that night greets as evening.
h.need(_tod(9) == "morning", "9am is not morning")
h.need(_tod(14) == "afternoon", "2pm is not afternoon")
h.need(_tod(19) == "evening", "7pm is not evening")
h.need(_tod(23) == "evening" and _tod(2) == "evening",
       "a night message must greet as 'evening' (owner's rule)")
h.need(greet.greeting(datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc))
       == "Good evening, Boss.", "the greeting is not formatted right")

# 2. Yesterday's bill, not today's (today is zero at the first open).
yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
c = state._conn()
c.execute("INSERT INTO spend (ts,day,model,usd) VALUES (?,?,?,?)",
          (time.time() - 86400, yday, "m", 0.12))
c.commit()
c.close()
h.need(abs(state.spend_yesterday() - 0.12) < 0.001,
       f"spend_yesterday is wrong: {state.spend_yesterday()}")
h.need(state.spend_today() == 0.0, "today's spend should be zero at first open")

# 3. The brief uses the real greeting + yesterday's bill, even on a quiet day.
brief = worker._brief_text()
h.need(brief.startswith("Good "), f"the brief greeting is not time-aware: {brief[:20]!r}")
h.need("Morning, Boss." not in brief, "the brief still hard-codes 'Morning'")
h.need("Yesterday's API cost" in brief and "0.12" in brief,
       f"the brief does not report yesterday's bill: {brief!r}")

# 4. The spoken greeting is time-aware without an override, and honours one.
realtime.GREETING = ""
h.need(realtime.greeting_for().startswith("Good "),
       f"the spoken greeting is not time-aware: {realtime.greeting_for()!r}")
realtime.GREETING = "custom hello"
h.need(realtime.greeting_for() == "custom hello",
       "an explicit JARVIS_GREETING override is not honoured")
realtime.GREETING = ""

h.ok("GREETING_OK")
