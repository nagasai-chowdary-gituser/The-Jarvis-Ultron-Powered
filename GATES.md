# GATES — jobs delete/display + intelligent WhatsApp monitoring

Owner's bugs (2026-10-25):
1. Jobs cannot be deleted ("delete the job" does nothing) and don't show on the
   Ultron interface. Every job must be visible; "delete job N" / "remove all
   jobs" must actually remove them, immediately.
2a. Watched-chat replies take 10-15s (poll interval + re-search, NOT
    screenshots). Make it faster.
2b. Jarvis must JUDGE (human-like intelligence) when a chat message is important
    - money owed, a call/meeting request, a decision needed, urgent/deadline -
    and proactively CALL the owner with a humanized alert ("your friend Kapcut
    says you took his 10,000 and wants it now"); if the call fails or is not
    picked, send Telegram. In a watched chat it still replies on his behalf AND
    alerts. Scope: only chats he tells it to watch.

## G1 — jobs can be deleted; deletion is real
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -c "import os,tempfile; os.environ['JARVIS_DB']=tempfile.mktemp(suffix='.db'); import state; a=state.add_job('reminder',{'text':'x'}); b=state.add_job('reminder',{'text':'y'}); assert len(state.jobs())==2; assert state.delete_job(a); assert len(state.jobs())==1; n=state.delete_all_jobs(); assert n==1 and len(state.jobs())==0; print('JOBS_DELETE_OK')"
  EXPECT: JOBS_DELETE_OK

## G2 — chat commands route to job deletion [manual]
"delete all jobs" / "remove all jobs" / "stop all jobs" delete every job;
"delete job 10" deletes that one. Verified by the fastpath routing + a live
run showing the jobs gone and the LIVE WATCH panel updating.

## G3 — a watched chat replies fast (short poll interval)
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -c "import worker; assert worker.WATCH_INTERVAL_S <= 6, worker.WATCH_INTERVAL_S; print('FAST_WATCH_OK', worker.WATCH_INTERVAL_S)"
  EXPECT: FAST_WATCH_OK

## G4 — urgent message in a watched chat alerts the owner (call -> telegram)
Verified by test: a watched-chat incoming message like "boss you took my 10000,
I want it now" produces an alert that calls/messages the owner with a humanized
summary, while an ordinary "hi how are you" does not.
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -m pytest tests/test_monitoring.py -q -p no:cacheprovider
  EXPECT: passed

## G5 — full suite stays green
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
  EXPECT: passed
