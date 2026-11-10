"""
The site playbooks (Jarvis's 'training' - curated knowledge of how popular sites
work) and the single-tab guard that stops it opening 5-8 tabs. All offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import playbooks  # noqa: E402


# ── the knowledge base ────────────────────────────────────────────────────────

def test_chatgpt_playbook_teaches_one_tab_and_no_screenshots():
    block = playbooks.block("make a ppt in chatgpt in my main account")
    assert block
    low = block.lower()
    assert "chatgpt" in low
    assert "one tab" in low                      # the 8-tabs bug, named
    assert "read_window" in block                # accessibility, not pixels
    assert "screenshot" in low                   # warns against screenshots (cost)


def test_each_taught_site_matches_its_words():
    assert playbooks.match("message kapcut on whatsapp")
    assert playbooks.match("send an email to priya")
    assert playbooks.match("play a song on youtube")
    assert playbooks.match("ask chatgpt to summarise this")


def test_an_unrelated_request_injects_nothing():
    # no cost on an ordinary turn, and the cached prompt stays byte-identical
    assert playbooks.match("what is my cpu usage") == []
    assert playbooks.block("what is my cpu usage") == ""


def test_block_includes_only_the_site_in_play():
    block = playbooks.block("open chatgpt and ask it something")
    assert "ChatGPT" in block
    assert "WhatsApp" not in block               # an unrelated site is not injected


# ── one tab, not eight ────────────────────────────────────────────────────────

def test_open_chrome_profile_does_not_stack_tabs(monkeypatch):
    import lib.desktop as d

    monkeypatch.setattr(d, "chrome_profiles",
                        lambda: [{"dir": "Default", "name": "Main",
                                  "email": "x@y.com", "pos": 1}])
    monkeypatch.setattr(d, "_match_profile", lambda which, profs: profs[0])
    monkeypatch.setattr(d, "_chrome_exe", lambda: "chrome.exe")
    opens = []
    monkeypatch.setattr(d.subprocess, "Popen", lambda cmd, **k: opens.append(cmd))
    d._LAST_OPEN.clear()

    r1 = d.open_chrome_profile("main", "https://chatgpt.com")
    r2 = d.open_chrome_profile("main", "https://chatgpt.com")   # the retry
    assert r1["ok"] and r1["reused"] is False
    assert r2["ok"] and r2["reused"] is True     # reused the open tab
    assert len(opens) == 1                        # Chrome launched ONCE, one tab

    r3 = d.open_chrome_profile("main", "https://gmail.com")     # a different site
    assert r3["reused"] is False and len(opens) == 2            # that one does open


# ── typing into a web field (the ChatGPT box) must work, not collapse ─────────

class _VPok:
    def SetValue(self, t):
        self.v = t


class _VPfail:
    def SetValue(self, t):
        raise Exception("contenteditable has no ValuePattern")


def _fake_field(monkeypatch, value_pattern):
    import lib.desktop as d

    class Target:
        def GetValuePattern(self):
            return value_pattern

        def SetFocus(self):
            pass

        def Click(self, **k):
            pass

    class Win:
        Name = "ChatGPT"

        def SetActive(self):
            pass

    monkeypatch.setattr(d, "_find_window", lambda t: Win())
    monkeypatch.setattr(d, "_find_control", lambda w, c, kinds=None: Target())
    typed = []
    from lib import cursor
    monkeypatch.setattr(cursor, "type_text", lambda t: typed.append(t))
    return d, typed


def test_set_control_native_field_uses_setvalue(monkeypatch):
    d, typed = _fake_field(monkeypatch, _VPok())
    out = d.set_control("Settings", "Name", "hello")
    assert out["ok"] and out["how"] == "setvalue" and typed == []   # no typing needed


def test_set_control_web_box_falls_back_to_typing(monkeypatch):
    """ChatGPT's message box is a contenteditable with no ValuePattern, so
    SetValue fails - it must FOCUS and TYPE instead of giving up (which would
    collapse the cheap path to screenshots)."""
    d, typed = _fake_field(monkeypatch, _VPfail())
    out = d.set_control("ChatGPT", "Message ChatGPT", "make a 4 slide deck on mars")
    assert out["ok"] and out["how"] == "typed"
    assert typed == ["make a 4 slide deck on mars"]                 # typed into the web box
