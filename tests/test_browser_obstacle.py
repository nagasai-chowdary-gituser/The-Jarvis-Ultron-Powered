"""
Browser recovery (#gap 7): the driver can tell a WALL from the page it wanted -
a login it can fix (owner signs in once) vs a captcha it must never try to
solve or pretend past. classify_obstacle is pure; no Chrome, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import browser  # noqa: E402


def test_captcha_is_detected():
    assert browser.classify_obstacle("Please verify you are human",
                                     "https://x.com") == "captcha"
    assert browser.classify_obstacle("Checking your browser before you access",
                                     "") == "captcha"
    assert browser.classify_obstacle("", "https://www.site.com/recaptcha") == "captcha"


def test_login_is_detected():
    assert browser.classify_obstacle("Sign in to continue",
                                     "https://platform.openai.com") == "login"
    assert browser.classify_obstacle("Enter your password", "") == "login"


def test_captcha_wins_over_login():
    # A challenge page often ALSO shows a sign-in link; sending the owner to
    # sign in at a wall he cannot clear is the wrong move, so captcha wins.
    assert browser.classify_obstacle(
        "Verify you are human. Already have an account? Sign in.", "") == "captcha"


def test_a_normal_page_has_no_obstacle():
    assert browser.classify_obstacle(
        "Top stories today. Breaking news and analysis.",
        "https://news.example.com") == ""
    assert browser.classify_obstacle("", "") == ""
