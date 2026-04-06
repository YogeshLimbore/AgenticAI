"""
browser/driver.py — Chrome driver factory with anti-detection measures.
Features:
  - Session cookie persistence (avoids re-login every run)
  - Randomized User-Agent
  - Human-like timing helpers
  - Graceful shutdown support
  - Docker-safe Chrome options
  - Local Windows + Docker support
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import List

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager

from utils.logger import get_logger

log = get_logger("browser")

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

COOKIE_FILE = Path("memory/session_cookies.json")


def _in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"


def _resolve_local_chromedriver_path() -> str:
    """
    webdriver-manager can sometimes return the wrong file in some environments.
    This fixes that for local runs.
    """
    installed_path = Path(ChromeDriverManager().install())

    if installed_path.is_file() and "THIRD_PARTY_NOTICES" not in installed_path.name:
        return str(installed_path)

    candidate_names = {"chromedriver", "chromedriver.exe"}
    for candidate in installed_path.parent.rglob("*"):
        if candidate.is_file() and candidate.name in candidate_names:
            return str(candidate)

    raise FileNotFoundError(
        f"Could not locate a valid chromedriver binary. webdriver-manager returned: {installed_path}"
    )


def create_driver() -> webdriver.Chrome:
    """
    Create a Chrome driver that works both:
      - locally on Windows/macOS/Linux
      - inside Docker
    """
    options = webdriver.ChromeOptions()
    ua = random.choice(_USER_AGENTS)

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={ua}")

    options.add_experimental_option(
        "excludeSwitches", ["enable-automation", "enable-logging"]
    )
    options.add_experimental_option("useAutomationExtension", False)

    if _in_docker():
        options.add_argument("--headless=new")
        options.binary_location = "/usr/bin/google-chrome"
        service = Service("/usr/local/bin/chromedriver")
        log.info("Using Docker Chrome + chromedriver")
    else:
        options.add_argument("--start-maximized")
        driver_path = _resolve_local_chromedriver_path()
        service = Service(driver_path)
        log.info(f"Using local chromedriver: {driver_path}")

    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
    except Exception as e:
        log.debug(f"Could not patch navigator.webdriver: {e}")

    try:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {"userAgent": ua},
        )
    except Exception as e:
        log.debug(f"Could not override CDP user agent: {e}")

    driver.set_page_load_timeout(40)
    log.debug("Chrome driver created with anti-detection settings")
    return driver


def save_session_cookies(driver: webdriver.Chrome):
    try:
        COOKIE_FILE.parent.mkdir(exist_ok=True)
        cookies = driver.get_cookies()
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f)
        log.debug(f"Session cookies saved ({len(cookies)} cookies)")
    except Exception as e:
        log.warning(f"Could not save session cookies: {e}")


def load_session_cookies(driver: webdriver.Chrome) -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        with open(COOKIE_FILE, encoding="utf-8") as f:
            cookies = json.load(f)

        driver.get("https://www.naukri.com")
        human_sleep(1.5, 2.5)

        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception:
                pass

        driver.refresh()
        human_sleep(2, 3)
        log.debug(f"Loaded {len(cookies)} session cookies")
        return True
    except Exception as e:
        log.warning(f"Could not load session cookies: {e}")
        return False


def clear_session_cookies():
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
        log.info("Session cookies cleared")


def human_sleep(a: float = 1.0, b: float = 2.5):
    base = random.uniform(a, b)
    jitter = random.uniform(-0.15, 0.15)
    time.sleep(max(0.3, base + jitter))


def wait_for(driver, by, selector, timeout: int = 15):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, selector))
    )


def safe_click(driver, element) -> bool:
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", element
        )
        time.sleep(random.uniform(0.3, 0.7))
        element.click()
        return True
    except (ElementClickInterceptedException, WebDriverException):
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


def close_popups(driver):
    selectors = [
        ".crossIcon", ".close", ".close-btn",
        "button[title='Close']", ".nI-gNb-crossIcon",
        ".chatbot_Nav", ".naukicon-cross", ".nI-gNb-close",
    ]
    for sel in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                safe_click(driver, el)
                human_sleep(0.1, 0.4)
        except Exception:
            pass


def dump_debug_page(driver, name: str, debug_dir: Path):
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir.mkdir(exist_ok=True)
    html_file = debug_dir / f"{name}_{ts}.html"
    png_file = debug_dir / f"{name}_{ts}.png"
    try:
        html_file.write_text(driver.page_source, encoding="utf-8")
        driver.save_screenshot(str(png_file))
        log.debug(f"Debug snapshot saved: {html_file}")
    except Exception as e:
        log.warning(f"Could not save debug snapshot: {e}")


def normalize_text(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def first_text_from_selectors(root, selectors: List[str]) -> str:
    for sel in selectors:
        try:
            el = root.find_element(By.CSS_SELECTOR, sel)
            text = el.text.strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def first_element_from_selectors(root, selectors: List[str]):
    for sel in selectors:
        try:
            return root.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            continue
    return None