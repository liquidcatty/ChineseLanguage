import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "screenshots"
SAMPLE = json.loads((ROOT / "stories" / "sample.json").read_text(encoding="utf-8"))
ALL_RT = sum(
    len(token["pinyin"])
    for sentence in SAMPLE["tokens"]
    for token in sentence
    if token.get("pinyin")
)
NEW_RT = sum(
    len(token["pinyin"])
    for sentence in SAMPLE["tokens"]
    for token in sentence
    if token.get("status") == "oov" and token.get("pinyin")
)
TITLE = SAMPLE["title"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def server():
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env={**os.environ, "STORY_BACKEND": "files"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/api/stories", timeout=0.5).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        process.terminate()
        raise RuntimeError("uvicorn did not start")
    yield BASE
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - depends on local install
            pytest.skip(f"Chromium is not installed: {exc}")
        yield instance
        instance.close()


def open_sample(page, width=1280, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE + "/read/sample", wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('#title')?.textContent === " + json.dumps(TITLE)
    )
    page.wait_for_selector("#story .w")


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    instance = context.new_page()
    open_sample(instance)
    yield instance
    context.close()


def test_library_filters_and_loads(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".story-tile")

    assert page.locator("#generate").count() == 0
    assert page.locator("#length").count() == 0
    assert page.locator(".story-tile").count() >= 3
    assert page.locator(".finished-check").count() >= 3
    assert page.locator("#mode-reading").get_attribute("aria-selected") == "true"

    page.select_option("#library-level", "3")
    page.wait_for_timeout(100)
    heads = page.locator(".story-group-head").all_inner_texts()
    assert heads and all(text.startswith("HSK 3") for text in heads)

    page.fill("#library-search", "过河")
    page.wait_for_timeout(100)
    assert page.locator('.story-tile[data-id="sample"]').count() == 1
    page.locator('.story-tile[data-id="sample"] .story-link').click()
    page.wait_for_url("**/read/sample")
    page.wait_for_function(
        "() => document.querySelector('#title')?.textContent === " + json.dumps(TITLE)
    )
    page.wait_for_selector("#story .w")
    assert page.locator("#generate").count() == 0
    assert page.locator("#length").count() == 0
    assert page.locator("#pinyin-toggle").count() == 1

    page.locator(".back-link").click()
    page.wait_for_url(BASE + "/")
    page.wait_for_selector(".story-tile")
    context.close()


def test_listening_section_and_finished(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_selector(".story-tile")

    page.click("#mode-listening")
    page.wait_for_timeout(100)
    assert page.locator("#mode-listening").get_attribute("aria-selected") == "true"
    link = page.locator('.story-tile[data-id="sample"] .story-link')
    assert "/listen/sample" in link.get_attribute("href")
    link.click()

    page.wait_for_url("**/listen/sample")
    page.wait_for_function(
        "() => document.querySelector('#title')?.textContent === " + json.dumps(TITLE)
    )
    assert page.locator("#play").is_visible()
    assert page.locator("#transcript").is_hidden()
    assert page.locator("#finished-check").count() == 1

    page.click("#transcript-toggle")
    assert page.locator("#transcript").is_visible()
    assert "过河" in page.inner_text("#transcript")

    assert page.locator("#quiz").is_visible()
    assert page.locator("#quiz .q").count() == 3
    page.click('.q[data-q="0"] .opt[data-opt="B"]')
    page.click('.q[data-q="1"] .opt[data-opt="B"]')
    page.click('.q[data-q="2"] .opt[data-opt="C"]')
    page.click("#check")
    page.wait_for_function(
        "() => document.querySelector('#score').textContent === 'Score: 2/3'"
    )
    assert page.locator("#quiz .opt.wrong").count() == 1
    assert page.locator("#quiz .quiz-evidence").count() >= 1

    checkbox = page.locator("#finished-check")
    checkbox.check()
    page.wait_for_timeout(300)
    page.reload(wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('#title')?.textContent === " + json.dumps(TITLE)
    )
    assert page.locator("#finished-check").is_checked()

    page.locator("#finished-check").uncheck()
    page.wait_for_timeout(300)
    page.reload(wait_until="networkidle")
    page.wait_for_function(
        "() => document.querySelector('#title')?.textContent === " + json.dumps(TITLE)
    )
    assert not page.locator("#finished-check").is_checked()

    page.locator(".back-link").click()
    page.wait_for_url(BASE + "/")
    page.wait_for_selector(".story-tile")
    context.close()


def test_word_popover_shows_pinyin_and_glosses(page):
    page.locator('#story .w[data-w="小文"]').first.click()
    popover = page.locator("#popover")
    popover.wait_for(state="visible")

    text = popover.inner_text()
    assert "xiǎo wén" in text
    assert "Xiaowen (imię)" in text
    assert "Xiaowen (name)" in text

    box = popover.bounding_box()
    assert box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= 1280
    assert box["y"] + box["height"] <= 900

    page.keyboard.press("Escape")
    page.wait_for_selector("#popover", state="hidden")

    page.locator('#story .w[data-w="小文"]').first.click()
    popover.wait_for(state="visible")
    page.locator("#title").click()
    page.wait_for_selector("#popover", state="hidden")


def test_quiz_words_are_clickable_without_selecting(page):
    option = page.locator('#quiz .opt[data-opt="A"]').first
    option.locator(".w").first.click()
    popover = page.locator("#popover")
    popover.wait_for(state="visible")

    text = popover.inner_text()
    assert "qù" in text
    assert "iść" in text
    assert "selected" not in (option.get_attribute("class") or "")

    page.keyboard.press("Escape")


def test_pinyin_modes_and_line_height(page):
    line_height = page.evaluate(
        "getComputedStyle(document.querySelector('#story')).lineHeight"
    )
    assert line_height == "67.2px"
    assert page.locator("#story rt").count() == 0

    page.click("#pinyin-toggle")
    assert page.inner_text("#pinyin-toggle") == "Pinyin: new words"
    assert page.locator("#story rt").count() == NEW_RT
    assert page.locator("#quiz rt").count() == 0
    assert (
        page.evaluate("getComputedStyle(document.querySelector('#story')).lineHeight")
        == line_height
    )

    page.click("#pinyin-toggle")
    assert page.inner_text("#pinyin-toggle") == "Pinyin: all"
    assert page.locator("#story rt").count() == ALL_RT
    assert page.locator("#quiz rt").count() == 0

    page.click("#pinyin-toggle")
    assert page.inner_text("#pinyin-toggle") == "Pinyin: off"
    assert page.locator("#story rt").count() == 0


def test_quiz_feedback_marks_and_scrolls_evidence(page):
    page.click('.q[data-q="0"] .opt[data-opt="B"]')
    page.click('.q[data-q="1"] .opt[data-opt="B"]')
    page.click('.q[data-q="2"] .opt[data-opt="C"]')
    page.click("#check")

    page.wait_for_function(
        "() => document.querySelector('#score').textContent === 'Score: 2/3'"
    )
    assert page.locator("#quiz .opt.wrong").count() == 1
    assert page.locator("#quiz .opt.correct").count() == 3

    wrong_color = page.locator("#quiz .opt.wrong").first.evaluate(
        "el => getComputedStyle(el).borderColor"
    )
    assert wrong_color == "rgb(208, 69, 59)"
    correct_color = page.locator("#quiz .opt.correct").first.evaluate(
        "el => getComputedStyle(el).borderColor"
    )
    assert correct_color == "rgb(58, 143, 75)"

    evidence = page.locator("#story .sentence.evidence")
    assert evidence.count() >= 1
    page.wait_for_timeout(900)
    box = evidence.first.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 900

    SHOTS.mkdir(exist_ok=True)
    page.screenshot(path=str(SHOTS / "quiz.png"), full_page=True, timeout=60000)


def test_mobile_popover_stays_inside(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    open_sample(page, 390, 844)

    page.locator('#story .w[data-w="奶奶"]').first.click()
    popover = page.locator("#popover")
    popover.wait_for(state="visible")
    box = popover.bounding_box()

    assert box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= 390
    assert box["y"] + box["height"] <= 844
    assert page.evaluate("document.documentElement.scrollWidth") == 390
    context.close()


def test_screenshots(browser):
    SHOTS.mkdir(exist_ok=True)

    library_context = browser.new_context(viewport={"width": 1280, "height": 900})
    library_page = library_context.new_page()
    library_page.goto(BASE, wait_until="networkidle")
    library_page.wait_for_selector(".story-tile")
    library_page.screenshot(path=str(SHOTS / "library-desktop.png"), timeout=30000)
    library_page.click("#mode-listening")
    library_page.wait_for_timeout(100)
    library_page.screenshot(path=str(SHOTS / "library-listening.png"), timeout=30000)
    library_page.set_viewport_size({"width": 390, "height": 844})
    library_page.reload(wait_until="networkidle")
    library_page.wait_for_selector(".story-tile")
    library_page.screenshot(path=str(SHOTS / "library-mobile.png"), timeout=30000)
    library_context.close()

    listen_context = browser.new_context(viewport={"width": 1280, "height": 900})
    listen_page = listen_context.new_page()
    listen_page.goto(BASE + "/listen/sample", wait_until="networkidle")
    listen_page.wait_for_selector("#play")
    listen_page.click("#transcript-toggle")
    listen_page.screenshot(path=str(SHOTS / "listening.png"), timeout=30000)
    listen_context.close()

    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    open_sample(page)
    page.click("#pinyin-toggle")
    page.click("#pinyin-toggle")
    page.screenshot(path=str(SHOTS / "desktop.png"), full_page=True, timeout=60000)

    page.set_viewport_size({"width": 390, "height": 844})
    open_sample(page, 390, 844)
    page.screenshot(path=str(SHOTS / "mobile.png"), full_page=True, timeout=60000)

    page.locator('#story .w[data-w="小文"]').first.click()
    page.screenshot(path=str(SHOTS / "mobile-popover.png"), timeout=30000)
    page.keyboard.press("Escape")
    context.close()

