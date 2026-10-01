# Gates: Jarvis — reliable autonomous Windows desktop agent

OWNS: *.py, lib/**, tests/**, GATES.md

Scope: Repair and upgrade the existing Jarvis repository into a genuinely
reliable autonomous Windows agent — real task lifecycle with cancellation,
dependency-aware parallelism with exclusive GUI ownership, deterministic
verification of every claimed outcome, bounded recovery with strategy change,
a 30-minute trusted permission session, and no silent failures — proven by
behavioural checks that exercise the real application, real filesystem, real
processes and the real browser rather than inspecting source.

Each CHECK runs a behavioural script under `tests/behavior/`. Every script
asserts against the real world and prints its success token only after every
assertion in it has passed, so the token cannot appear on a partial run.

## Task lifecycle and persistence

- [x] G1: A task moves through its real states and every transition is persisted
  CHECK: .venv\Scripts\python.exe tests\behavior\\t01_lifecycle.py
  EXPECT: LIFECYCLE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=361a593da41230e0be0bbd29b501261f9de666e035c0e5787b828a89ba5de0a2; exit=0; EXPECT=matched; output-sha256=e35efb4a41371fade90cce9482a921fbaf7c2517957724005be6cc85f3763ca4; output-bytes=14; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G2: Task state survives a process restart and is reloaded from disk
  CHECK: .venv\Scripts\python.exe tests\behavior\\t02_persistence.py
  EXPECT: PERSISTENCE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=15cb2a7224746c0e8f762daa8b4bc9db301e3ac5569e805b11d2773c3c2f43c2; exit=0; EXPECT=matched; output-sha256=9bffe96c1f0617c20dca846e0366d9a9a339eda48e4c2b0aa5645d972c7dedf5; output-bytes=16; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G3: An interrupted running task is recovered, not left stuck in EXECUTING
  CHECK: .venv\Scripts\python.exe tests\behavior\\t03_recovery.py
  EXPECT: RECOVERY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=37948f81a0d2378340fb0293043859c3fd22b2cf6af7867c9c5c4481a4b5b002; exit=0; EXPECT=matched; output-sha256=2610d66af90d6b9745de7ff3b9aacd485500c6c78f4ea71cf33e58a2e3d23ffb; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G4: Failure is an explicit terminal state carrying the real error
  CHECK: .venv\Scripts\python.exe tests\behavior\\t04_failure_state.py
  EXPECT: FAILURE_STATE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=b4de7e5a6784a4811305649d0b79e86e98200971d33bff74d5a9a67820e6b8ab; exit=0; EXPECT=matched; output-sha256=505bc472c89e886d2523671cfa822f6669a641ef165185dcec02442d2305ffdb; output-bytes=18; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Cancellation, pause, resume

- [x] G5: Cancelling a running task actually stops execution, not just a label
  CHECK: .venv\Scripts\python.exe tests\behavior\\t05_cancel.py
  EXPECT: CANCEL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=6ef5565e0e83bcce965f1a11eb1ae546a032aa49d3436c481afe6b80feb39f1b; exit=0; EXPECT=matched; output-sha256=6ba8ab839479a49b275b4ccf56ffdadb7b04f4b3f2ef0c7482335f37a0cc6dfe; output-bytes=11; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G6: Pause halts work and resume continues it from where it stopped
  CHECK: .venv\Scripts\python.exe tests\behavior\\t06_pause_resume.py
  EXPECT: PAUSE_RESUME_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c1c750b735747bc2fa94ce19d9cd55589fd9cb3963db63a87fc1d7ee324e6fb6; exit=0; EXPECT=matched; output-sha256=1c05a8e78223a258b5d85fd02d1d7759dc9c16289565c5567d32d8cf4ca83c4e; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G7: A cancelled task releases its resources and leaves no live worker
  CHECK: .venv\Scripts\python.exe tests\behavior\\t07_cancel_cleanup.py
  EXPECT: CANCEL_CLEANUP_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=2cd52fa15fe79e31093883b6ca7c8b6e4837325b3c49ad4296cf8145937868a0; exit=0; EXPECT=matched; output-sha256=387384debd341312cdd28433d20f1f8e819ada6cda6a679e9966054731503993; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Deterministic executors against the real machine

- [x] G8: Filesystem operations create, read, move, copy and organise real files
  CHECK: .venv\Scripts\python.exe tests\behavior\\t08_filesystem.py
  EXPECT: FILESYSTEM_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=a711e89a3c1b8b6a9c0dbe7332bee92294d32d0ad73849702f2c7d4af13f0c7d; exit=0; EXPECT=matched; output-sha256=29e14ee44b9421cf11121b47c823838b87d1be80317ddfb1a2afde281ecbf407; output-bytes=15; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G9: Terminal execution returns real stdout, stderr and exit codes
  CHECK: .venv\Scripts\python.exe tests\behavior\\t09_terminal.py
  EXPECT: TERMINAL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=0d00f1ea322d4757c3fba82c27dba1fde66437e4ce415e23374f475a88f56dc2; exit=0; EXPECT=matched; output-sha256=5cd20491d9c03325578740eb30fb062d3a14adbb8274cc622f459de94a40139b; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G10: A terminal timeout kills the process and leaves no orphan behind
  CHECK: .venv\Scripts\python.exe tests\behavior\\t10_terminal_timeout.py
  EXPECT: TERMINAL_TIMEOUT_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=aac92095caa55f7827b4dede5ccecadc28ad09062c1fa92ec54b52d04e0a9b11; exit=0; EXPECT=matched; output-sha256=748a5d3fcfeae949381137b01c55059a2937c91b4990563b409c35a6b7c43b7a; output-bytes=21; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G11: A real application launches and is verified by process and window state
  CHECK: .venv\Scripts\python.exe tests\behavior\\t11_app_launch.py
  EXPECT: APP_LAUNCH_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=99c3e71758db6cf9a4df8a82579213112a70ba120d45cb397de9020d962d5d24; exit=0; EXPECT=matched; output-sha256=528f4304a55f4bd823a2abf8207798328320c689ee07490f5e2d99ba76569413; output-bytes=15; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G12: Launching an unknown application fails loudly and opens nothing
  CHECK: .venv\Scripts\python.exe tests\behavior\\t12_app_launch_fail.py
  EXPECT: APP_LAUNCH_FAIL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=da2e1b5edb92f9d920168946bc0c110b6776dadac99e6237c92f3c7cb6b9f343; exit=0; EXPECT=matched; output-sha256=690af8889ed56dfa1d1051b8e343c88cb15f5bdc5073b0d433fd37f37584cd75; output-bytes=20; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G13: Process management lists, inspects and terminates real processes
  CHECK: .venv\Scripts\python.exe tests\behavior\\t13_process.py
  EXPECT: PROCESS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=cdabb3a87bbdb81b766c2cc33c0f86fdd94d8f32dfe95453fa02f798b4b73633; exit=0; EXPECT=matched; output-sha256=56287a5a5595212f86e94941695bfdf9fc7a3018b9f89d820c6517948eacb86f; output-bytes=12; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Browser as a first-class capability

- [x] G14: The browser launches, navigates and the landed URL is verified
  CHECK: .venv\Scripts\python.exe tests\behavior\\t14_browser_nav.py
  EXPECT: BROWSER_NAV_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=0d3c495d0781d3a9ebd347f6d07c0efba6235d84362b024ecdbe450b2c5266bb; exit=0; EXPECT=matched; output-sha256=483a81966520b746ce40e20e8ac09e6bc75d371fbf5ad0d53cbe1aa206da20cd; output-bytes=16; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G15: The browser reads real DOM content rather than guessing from pixels
  CHECK: .venv\Scripts\python.exe tests\behavior\\t15_browser_read.py
  EXPECT: BROWSER_READ_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=9bd8b53630e4f78842cf07151287a2a559ab1f47c5744307120d9b69e138e134; exit=0; EXPECT=matched; output-sha256=453fce1d845a7b5ebb5c37a5f01f6c7d6bc48d5742034c97429a7b0f48c352c9; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G16: Navigating to a dead host is detected and recovered, not reported as success
  CHECK: .venv\Scripts\python.exe tests\behavior\\t16_browser_error.py
  EXPECT: BROWSER_ERROR_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=115d044bc9928a1629119b8abbe2a713d9c66111a293bae053f3cdcfadfcfd51; exit=0; EXPECT=matched; output-sha256=89a7c60610338188c0c6db2f990e944e9dd199ba15d29be646d04583afe7e9a7; output-bytes=18; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G17: Multiple tabs are opened, switched between and closed without leaks
  CHECK: .venv\Scripts\python.exe tests\behavior\\t17_browser_tabs.py
  EXPECT: BROWSER_TABS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=1a02b2f7101bab109d2bdf1a73475513fdbc8dd7765375daca96b680a4d10d78; exit=0; EXPECT=matched; output-sha256=82f2f1f881549fdfccfe4493b81a60422414263568dd21d3ec88ac765ca2e422; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Dependency-aware parallelism with exclusive GUI ownership

- [x] G18: Independent work runs concurrently and finishes faster than serially
  CHECK: .venv\Scripts\python.exe tests\behavior\\t18_parallel.py
  EXPECT: PARALLEL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=1c884085637673a1800be45eaa71e11838b28bdfb8bcb66d1e6613bcae509cdc; exit=0; EXPECT=matched; output-sha256=679862e60be677c734c911c367ba5ddbf214e1c0b711b39df24428df27ec3034; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G19: Two GUI operations never hold the desktop at the same time
  CHECK: .venv\Scripts\python.exe tests\behavior\\t19_gui_lock.py
  EXPECT: GUI_LOCK_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=8e3209b9993d7c299d6e0424ac94231fb11d1d02a6fcdb138133f4c16bfb651c; exit=0; EXPECT=matched; output-sha256=b261cfcfb79d2e3c589a609c89005fa8d12c0387fcb6da588b67bb6aa3c6fff1; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G20: A dependent step waits for its inputs while unrelated work proceeds
  CHECK: .venv\Scripts\python.exe tests\behavior\\t20_dependencies.py
  EXPECT: DEPENDENCIES_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=0b08a729112dfc64305fa715971404a5666dc21167a9aa470ac2d60f69a5eef3; exit=0; EXPECT=matched; output-sha256=66cee69fd7ba085ed8f6a5131e710246ccb8110bdb5e329e258f1b72748c60ab; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G21: Concurrency is bounded; no unlimited workers are ever created
  CHECK: .venv\Scripts\python.exe tests\behavior\\t21_bounded.py
  EXPECT: BOUNDED_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=ba8f5316c868d74ccd52a0954509dd17522bb66642e7869509adc6c33d10e2f0; exit=0; EXPECT=matched; output-sha256=4503020549dba1c6dbb41f6b1061aae356c265451fdbdb5a8e83dc1a968e27c9; output-bytes=12; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Verification, recovery and escalation

- [x] G22: A claimed outcome is verified against the real world before it is reported
  CHECK: .venv\Scripts\python.exe tests\behavior\\t22_verify_outcome.py
  EXPECT: VERIFY_OUTCOME_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=76ee7810b897eb5a23bdd9de1d5fdd144ecf536f40dd07a3fd75edf20facfc0d; exit=0; EXPECT=matched; output-sha256=e24d683721955c6c1c2cc2bd45d90ba54791887e7bc34ba8cf984e010e42b4fd; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G23: A repeated identical failure forces a strategy change, not another retry
  CHECK: .venv\Scripts\python.exe tests\behavior\\t23_retry_strategy.py
  EXPECT: RETRY_STRATEGY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=871f34aff78bd7cc3494eeb92519630823d1b69bb3a08656bbd382fa25a91972; exit=0; EXPECT=matched; output-sha256=89e98c2cb497ef2bd8149a068bd5e21665432273b6f79d82740afb213aa2129d; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G24: Retries are bounded and a genuinely blocked task escalates to the owner
  CHECK: .venv\Scripts\python.exe tests\behavior\\t24_escalation.py
  EXPECT: ESCALATION_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=ac17aecd895c40c1f5e90df94412c35d49c2ac6f54d87a2cbb831a5486351980; exit=0; EXPECT=matched; output-sha256=33bfad12b5a3833c10cc94f6cbe291ae9d5c079e67390437fe2ed7432511c1fa; output-bytes=15; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G25: An unearned completion claim is contradicted before it reaches the owner
  CHECK: .venv\Scripts\python.exe tests\behavior\\t25_honesty.py
  EXPECT: HONESTY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=b7c4a69faed7cbea85e0e7a73fd8257a2bc82f0bc0d4c65c357280576b88bdf0; exit=0; EXPECT=matched; output-sha256=d07cd44678e993a33bd0112ea740b3c5bd8a1056b211acbf8236337fe4deff84; output-bytes=12; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Permission session

- [x] G26: The correct PIN opens one trusted session; a wrong PIN opens none
  CHECK: .venv\Scripts\python.exe tests\behavior\\t26_pin_auth.py
  EXPECT: PIN_AUTH_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=46f9d6aa0fc648ebd33da561e5d2858f5972ccf0b0fd9cf700d7c2b5c5d12f0b; exit=0; EXPECT=matched; output-sha256=246b4886d178e7be1574a3b456b12e4ed35ea5d90779156175411570775301e7; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G27: Inside a trusted session ordinary work runs with zero further prompts
  CHECK: .venv\Scripts\python.exe tests\behavior\\t27_session_reuse.py
  EXPECT: SESSION_REUSE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=a39dda2f9cd893b4902172210d3a5975c0a2c0c9ec30b5b9218157ce8410cf68; exit=0; EXPECT=matched; output-sha256=d53ac2b358702a36942075ebc2a3b90604026b693935ea671948ddee51dc3b90; output-bytes=18; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G28: The trusted session expires after 30 minutes and stops being trusted
  CHECK: .venv\Scripts\python.exe tests\behavior\\t28_session_expiry.py
  EXPECT: SESSION_EXPIRY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=7e039ba7215052003e73bb918b674f17fb37a940de065d07c541c47cb5aab4ad; exit=0; EXPECT=matched; output-sha256=00e8d3c3f6cadc2735c5578e9b105065bca3b766f934bd3b2f7c99ea981bb4a3; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G29: Destructive and financial actions still require explicit authorisation
  CHECK: .venv\Scripts\python.exe tests\behavior\\t29_high_risk.py
  EXPECT: HIGH_RISK_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=67ed4aca94468eff47417a305580fb3952fcaffe1c68596bcd954f389669cb0d; exit=0; EXPECT=matched; output-sha256=ce1a7d8b00ba2ee8153e830ff6b928fadd674957473de4030f4532488c6f1c40; output-bytes=14; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G30: No secret, PIN or credential is ever written to a log or the database
  CHECK: .venv\Scripts\python.exe tests\behavior\\t30_no_secrets.py
  EXPECT: NO_SECRETS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=303b7281548d359b85e57ad0e97d40c40000a062a664c17649f22bce3b4a63ef; exit=0; EXPECT=matched; output-sha256=f7aad8c79d533d3f4c81aab4741bd6106411e4313014f405ad4436867192f969; output-bytes=15; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Speed, measured rather than asserted

- [x] G31: Deterministic requests are answered without any model call
  CHECK: .venv\Scripts\python.exe tests\behavior\\t31_fastpath.py
  EXPECT: FASTPATH_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=fcb4b4e7184b723ea09ceffc2530726a8f4e5df0ba17680c4b2b9f7e8eab2944; exit=0; EXPECT=matched; output-sha256=ea88f9fa956d62130ce5d12b55f22c4d71df10719fb14fd6ea7c3944bedcd899; output-bytes=13; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G32: Nothing sleeps on a fixed timer where a state condition is available
  CHECK: .venv\Scripts\python.exe tests\behavior\\t32_no_blind_waits.py
  EXPECT: NO_BLIND_WAITS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=4eaf7bf339b4ef15a1c3bcaeabb19b7f87c14aff044b392b8895823bd94a519d; exit=0; EXPECT=matched; output-sha256=aecfcc46e5b1db37b498b62a16e372e67ba42e9ab304d4c54d8daa9c2a76f560; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G33: The measured tool surface stays small enough to keep turns cheap
  CHECK: .venv\Scripts\python.exe tests\behavior\\t33_token_surface.py
  EXPECT: TOKEN_SURFACE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=060e5679ccbb68ca3876997b1b68196af3c1ad01b0d7a294ae0fd5b27f7a5eae; exit=0; EXPECT=matched; output-sha256=069dc50010541f93dfa4f628c0f7e1b22a4ed354d728be4b277a8ddd435d0793; output-bytes=63; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## No silent failures, no leaks

- [x] G34: No exception is swallowed without being recorded somewhere
  CHECK: .venv\Scripts\python.exe tests\behavior\\t34_no_silent_failure.py
  EXPECT: NO_SILENT_FAILURE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=125526b798a9add16a6a3ab8731d1aad49b1898a052aaea182b55b465f67ab55; exit=0; EXPECT=matched; output-sha256=c8c2861a2ab4f51664c9d371697b35cf87feed0adcac84e52c25efe26dfbda1f; output-bytes=22; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G35: Repeated heavy cycles leak no processes, threads or browser contexts
  CHECK: .venv\Scripts\python.exe tests\behavior\\t35_leaks.py
  EXPECT: LEAKS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=db01239121091ce0ce92b2b228fada1bb1c06f086e13d89e12d18445838e06a3; exit=0; EXPECT=matched; output-sha256=375a1dc9e15b047e4bfee6c0ca2c164f459151a20520a478b0670b1ecc0cf8af; output-bytes=10; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G36: Every claimed capability in the library index actually exists and runs
  CHECK: .venv\Scripts\python.exe tests\behavior\\t36_lib_contract.py
  EXPECT: LIB_CONTRACT_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=9b4af18520a9e1065610ae2063da55e5d1aecf597f3e6816d07768695fa1c100; exit=0; EXPECT=matched; output-sha256=a158d452db1e951aa2575fef29ef2394df57597c0468b64a590db1e4fda59d11; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## The application itself

- [ ] G37: The backend starts, serves health, and shuts down without orphans
  CHECK: .venv\Scripts\python.exe tests\behavior\\t37_startup.py
  EXPECT: STARTUP_OK
  EVIDENCE: pending

- [x] G38: Every transport reaches the same agent and can actually act
  CHECK: .venv\Scripts\python.exe tests\behavior\\t38_transports.py
  EXPECT: TRANSPORTS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=68fda4e2962f990f704ee537471dcf8d5f3cfcae0aec2197226cb8e27fbb312d; exit=0; EXPECT=matched; output-sha256=c1c12c56daf11718dfd924f59749f519f282a872fee032221833a12b9c898542; output-bytes=76; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G39: Conversation continuity survives across separate turns and restarts
  CHECK: .venv\Scripts\python.exe tests\behavior\\t39_continuity.py
  EXPECT: CONTINUITY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=62b181fc11206719acf0118b5cd4b5ff1e43d09b6cfc0359af28c395eae6667c; exit=0; EXPECT=matched; output-sha256=8e75aea37900f3a060aaa6836a9e92b627dd135c5a5027ed8f95b7d8c2190ef3; output-bytes=15; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G40: The regression suite still passes end to end
  CHECK: .venv\Scripts\python.exe tests\behavior\\t40_regression.py
  EXPECT: REGRESSION_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=7b98c9ef90027651f32b68a7fdda3d4b6ac4863010c8a8c4dbb39cf597dca1c5; exit=0; EXPECT=matched; output-sha256=15a13fed409af20575836179100cb63ca7d5c9b4967a2de3303d0c551e7fc29e; output-bytes=46; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Real end-to-end autonomy

- [x] G41: A real multi-step objective is planned and completed against the machine
  CHECK: .venv\Scripts\python.exe tests\behavior\\t41_e2e_multistep.py
  EXPECT: E2E_MULTISTEP_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c986446c908e5390d81b569a04850395e26e5bbc7d78cc9d3ead0a78e9304e68; exit=0; EXPECT=matched; output-sha256=72e5eacac0e0a366a6e4f9286981bfc0ef9c46624ec741af013dd46d05adac62; output-bytes=56; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G42: A combined browser plus filesystem objective produces the real artefact
  CHECK: .venv\Scripts\python.exe tests\behavior\\t42_e2e_browser_fs.py
  EXPECT: E2E_BROWSER_FS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=352f5c7c2eebb118ac149e206738cf47341fa04d6a0157f3800af86a94cccb24; exit=0; EXPECT=matched; output-sha256=223e38e12683f1d614c3d5e060b46e833da1ecd6caf09217e536cd943a8be5dd; output-bytes=54; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G43: A terminal plus task-state workflow completes and is verified on disk
  CHECK: .venv\Scripts\python.exe tests\behavior\\t43_e2e_terminal.py
  EXPECT: E2E_TERMINAL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=721431ac5d222c5e79ac76aad40ec382bd81283679b338b063645631108d32b4; exit=0; EXPECT=matched; output-sha256=6cb3df45d97929d87b15876598592709ffe40ca221000f3fe8cb595be575d382; output-bytes=50; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G44: The agent replans when the environment differs from what it expected
  CHECK: .venv\Scripts\python.exe tests\behavior\\t44_replanning.py
  EXPECT: REPLANNING_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=fbf7710c1da581aaf529b7718c690bcee80faecf8d3fa8df3f73fab75d7d5b95; exit=0; EXPECT=matched; output-sha256=32a9e32e0a645cfe9afb0ee7478f0d65d9d75a91fa4f4e7175a41b759528a15f; output-bytes=61; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G45: The same objective run repeatedly gives the same verified outcome
  CHECK: .venv\Scripts\python.exe tests\behavior\\t45_stability.py
  EXPECT: STABILITY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=1e988f12e30a19d011e645ff1459d1c2b5e5ad4693f055e71b8f2a03ff6de97b; exit=0; EXPECT=matched; output-sha256=c4e33720bd6601cc1aa9da3d0d13106252995d278cd1137a69f261cdcd8d4607; output-bytes=56; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Requires the owner's hardware and presence

- [ ] G46: A real phone call connects and Jarvis is audible on the owner's handset
  EVIDENCE: pending

- [ ] G47: The desk microphone captures speech and the agent answers aloud
  EVIDENCE: pending

<!--
G46 and G47 are manual because they need a human to answer a ringing phone and
to speak into a microphone. No command can decide them. They are recorded here
rather than dropped so that the report cannot silently imply voice coverage it
does not have.
-->

## Adaptive execution — the step-limit repair

- [x] G48: No legitimate task is ever ended by a low-level action count
  CHECK: .venv\Scripts\python.exe tests\behavior\t48_no_step_limit.py
  EXPECT: NO_STEP_LIMIT_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=2e63a1fee304542c02e1efdfccb3f085897d8e5df1bab2c6369f138a80047923; exit=0; EXPECT=matched; output-sha256=26be196045600f98cebd36f478c8fd873805e4ad5d9fe1461b4c6f192fda1c50; output-bytes=18; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G49: A task needing far more actions than the old limit still completes
  CHECK: .venv\Scripts\python.exe tests\behavior\t49_long_task.py
  EXPECT: LONG_TASK_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=9207846899b04086f0278e441de58bb81f17257f6e7228f6b6a0ad87479cb86a; exit=0; EXPECT=matched; output-sha256=474d6eb9c8ddad284973b41b78da36181f6fc13433d0e9da4f072bb563000474; output-bytes=321; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G50: Completion is a terminal condition reached by verifying the objective
  CHECK: .venv\Scripts\python.exe tests\behavior\t50_terminal_complete.py
  EXPECT: TERMINAL_COMPLETE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=2c8157b1b8660b20d170aca05147c7f05324fc4d7ce330893ba8aaf40daa5217; exit=0; EXPECT=matched; output-sha256=bfa39c073c094d181df6dd98050cb8a113d22eb75addddf380d75578fa408dd1; output-bytes=22; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G51: A genuinely unreachable objective ends as BLOCKED with the remedy
  CHECK: .venv\Scripts\python.exe tests\behavior\t51_terminal_blocked.py
  EXPECT: TERMINAL_BLOCKED_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=36520dfe2dca8db39b9e4c7a8065aee76e3c6c4ae9cc779947a7986c824a1cb6; exit=0; EXPECT=matched; output-sha256=5ac1e91b8d2b8e75a540d6f457cca71918069a97364c7dab7725b04e4f7fc3f7; output-bytes=21; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G52: A wall-clock deadline ends the task safely and says so
  CHECK: .venv\Scripts\python.exe tests\behavior\t52_terminal_timeout.py
  EXPECT: TERMINAL_TIMEOUT_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=3e046b23afd1b55f431457d7d9412c6c2626a6104abc4ff8e489870ca1ce5775; exit=0; EXPECT=matched; output-sha256=748a5d3fcfeae949381137b01c55059a2937c91b4990563b409c35a6b7c43b7a; output-bytes=21; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G53: Cancellation beats every other budget and stops work at once
  CHECK: .venv\Scripts\python.exe tests\behavior\t53_terminal_cancel.py
  EXPECT: TERMINAL_CANCEL_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=e981a16d439f0746357969f5881262eae20ffe6f85299e62007fb76382fca7b5; exit=0; EXPECT=matched; output-sha256=dcae0136d89f63db1e91a0f262a5159784f52412e07f30a1d7bcf15922317577; output-bytes=20; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G54: An identical action repeating without progress is detected as a loop
  CHECK: .venv\Scripts\python.exe tests\behavior\t54_loop_detection.py
  EXPECT: LOOP_DETECTION_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=26ee32178fc6341a1d247a8e278bfa5ed63a98eb95217f4563b233fb72545449; exit=0; EXPECT=matched; output-sha256=c4407dbc04effa71ba5747998fcedabebf0008a0aea76248ddc1f258cbe2fada; output-bytes=19; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G55: Work that changes nothing is detected as no-progress and forces a change
  CHECK: .venv\Scripts\python.exe tests\behavior\t55_no_progress.py
  EXPECT: NO_PROGRESS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=daf889644bbd1bfaed0e3f822ebf1d4a6d2818f0b81154a522ceed10ece0cb09; exit=0; EXPECT=matched; output-sha256=32df2356e86000895cbcf60bb114f093c19182882632ad587e8ecec69109acba; output-bytes=16; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G56: Progress is measured from real outcomes, not from tool calls happening
  CHECK: .venv\Scripts\python.exe tests\behavior\t56_progress_signals.py
  EXPECT: PROGRESS_SIGNALS_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c3e4f2e998f18ef4d3cf74c6dd39c7df1e87b11dddb3134878a3614d9b6551cd; exit=0; EXPECT=matched; output-sha256=61d212d1e538ddb347367b6bd89dddd37dbb497150477a0b34f39a6cc4cf9a0a; output-bytes=21; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G57: Recovery draws on its own budget and cannot consume the productive one
  CHECK: .venv\Scripts\python.exe tests\behavior\t57_recovery_budget.py
  EXPECT: RECOVERY_BUDGET_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=0dab52eb399bc958679309b27e22b6eecddb5f3f59ab479df096c4239b531ab1; exit=0; EXPECT=matched; output-sha256=1f3dc7b9c4c34c479eb5c4c4300a43ad04dc458b728d3f161875bbbbb422dd31; output-bytes=20; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G58: A blocked tool times out into recovery instead of freezing the task
  CHECK: .venv\Scripts\python.exe tests\behavior\t58_tool_timeout.py
  EXPECT: TOOL_TIMEOUT_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=0382c2e66dd270b41661abdae2de1d543b0f515da2ebda15d8eb7ae8677075f4; exit=0; EXPECT=matched; output-sha256=47dbf7188853c9be848799adea5abdcecc43f4469dd288882b043cdae1ad682b; output-bytes=17; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G59: A failing strategy is swapped for a different one, not repeated
  CHECK: .venv\Scripts\python.exe tests\behavior\t59_strategy_switch.py
  EXPECT: STRATEGY_SWITCH_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=cd2f9334b55fda0f028351702693bc0f65309a84106b482b9858e8caaa29e9b7; exit=0; EXPECT=matched; output-sha256=eb292ef5f5ca5b5941fce6111a69938a59964e657d1605466702317a8f0e8efa; output-bytes=20; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G60: A simple request stays simple and cheap under the new engine
  CHECK: .venv\Scripts\python.exe tests\behavior\t60_simple_stays_fast.py
  EXPECT: SIMPLE_STAYS_FAST_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=fb29b81b3099b4a3c1698246e67167098c5c296d4f47893ecbefec8d4836dd5b; exit=0; EXPECT=matched; output-sha256=271d446f40bf66cd1a4356a9df1c38adf4df4a790575f99643a0a8e183dcbb44; output-bytes=66; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

## Seeing the browser the owner is actually using

- [x] G61: Chrome instances are discovered and their reachability reported honestly
  CHECK: .venv\Scripts\python.exe tests\behavior\t61_chrome_discovery.py
  EXPECT: CHROME_DISCOVERY_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=4a0de9d5b7ab7adcb772574ddaf745c18aad66dfdc61c714f9424c33d3f74c2a; exit=0; EXPECT=matched; output-sha256=3d2f0e211ae1f675df318cc63715f0e638c49eccbd659ca2b2227403a84a9a2c; output-bytes=21; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [x] G62: An unreachable browser is reported with the exact remedy, never guessed at
  CHECK: .venv\Scripts\python.exe tests\behavior\t62_unreachable_browser.py
  EXPECT: UNREACHABLE_BROWSER_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=b078d0508fa179533f8be4bf651b72799251d1fc0fbf3eed3c861f426e010e75; exit=0; EXPECT=matched; output-sha256=275c586ea265c697f1a3855038b801bd1fd0e0e5963bbd1d23d64fe8d480441a; output-bytes=24; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [ ] G63: Dynamically rendered page content is read by escalating, not by a parser
  CHECK: .venv\Scripts\python.exe tests\behavior\t63_dynamic_content.py
  EXPECT: DYNAMIC_CONTENT_OK
  EVIDENCE: pending

- [ ] G64: The now-playing track and queue are read from a real media page
  CHECK: .venv\Scripts\python.exe tests\behavior\t64_media_state.py
  EXPECT: MEDIA_STATE_OK
  EVIDENCE: pending

- [ ] G65: Playback can be driven and the change verified in the real page
  CHECK: .venv\Scripts\python.exe tests\behavior\t65_media_control.py
  EXPECT: MEDIA_CONTROL_OK
  EVIDENCE: pending

- [ ] G66: The original failing request now ends on a real terminal condition
  CHECK: .venv\Scripts\python.exe tests\behavior\t66_original_failure.py
  EXPECT: ORIGINAL_FAILURE_OK
  EVIDENCE: pending

## Stress — the engine under pressure

- [x] G67: Twenty varied real workloads all end on legitimate terminal conditions
  CHECK: .venv\Scripts\python.exe tests\behavior\t67_stress_suite.py
  EXPECT: STRESS_SUITE_OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c8da76dc7418bccaf6804ba27e414b7cdf2ea246bca1936ba876670147cde81a; exit=0; EXPECT=matched; output-sha256=868b52337dc43e1299eac7c5116124ac26d206854925588b73484dde13ec57bb; output-bytes=2285; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries

- [ ] G68: Previously working capabilities still work under the new engine
  CHECK: .venv\Scripts\python.exe tests\behavior\t68_regression_capabilities.py
  EXPECT: REGRESSION_CAPABILITIES_OK
  EVIDENCE: pending

- [x] G69: All four what-plays-next strategies work on a site nobody wrote code for
  CHECK: .venv\Scripts\python.exe tests\behavior\t69_queue_strategies.py
  EXPECT: QUEUE-STRATEGIES-OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c2fcfbbbb83a6be6be169b79ba75886d70b4325e6ea8eaa061fb3d74975f8ca3; exit=0; EXPECT=matched; output-sha256=458436bb3e476994887b348b359ef43fe81e07f2e23fd0e510a548ddfb70bc5e; output-bytes=544; shell=C:\Windows\System32\cmd.exe; cwd=C:\Users\asus\Desktop\ultron; path=56d288f5bb34/48 entries
