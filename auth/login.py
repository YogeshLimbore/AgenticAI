"""
auth/login.py — Naukri login with session cookie persistence.
On first run: logs in with email/password and saves cookies.
On subsequent runs: loads saved cookies → skips login (faster + less suspicious).
"""

from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from browser.driver import (
    close_popups, dump_debug_page, human_sleep,
    load_session_cookies, save_session_cookies, wait_for,
)
from utils.logger import get_logger

log = get_logger("auth")

HOME_URL  = "https://www.naukri.com/mnjuser/homepage"
LOGIN_URL = "https://www.naukri.com/nlogin/login"


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    """Return True if we're on a non-login page after nav."""
    return "login" not in driver.current_url.lower()


def login(driver: webdriver.Chrome, email: str, password: str,
          debug_dir=None) -> bool:
    """
    Attempt login using saved session first, then fall back to
    email/password. Returns True on success.
    """
    log.info("Attempting session cookie login...")

    # Try saved session first
    if load_session_cookies(driver):
        driver.get(HOME_URL)
        human_sleep(2, 3)
        if _is_logged_in(driver):
            log.info("[green]Session login successful[/] (no password used)")
            return True
        log.info("Session cookies expired — falling back to password login")

    # Full login
    log.info("Logging in with email/password...")
    driver.get(LOGIN_URL)
    human_sleep(2.5, 4.0)

    try:
        email_field = wait_for(driver, By.ID, "usernameField", 20)
        email_field.clear()
        email_field.send_keys(email)
        human_sleep(0.5, 1.2)

        pwd_field = wait_for(driver, By.ID, "passwordField", 20)
        pwd_field.clear()
        pwd_field.send_keys(password)
        human_sleep(0.4, 0.9)
        pwd_field.send_keys(Keys.RETURN)

        human_sleep(4, 6)
        close_popups(driver)

        if _is_logged_in(driver):
            log.info("[green]Login successful![/]")
            save_session_cookies(driver)
            return True

        # One more attempt — navigate to homepage
        driver.get(HOME_URL)
        human_sleep(3, 5)
        close_popups(driver)

        if _is_logged_in(driver):
            log.info("[green]Login successful (second attempt)[/]")
            save_session_cookies(driver)
            return True

        log.error("Login failed — check credentials")
        if debug_dir:
            dump_debug_page(driver, "login_failed", debug_dir)
        return False

    except Exception as e:
        log.error(f"Login error: {e}")
        if debug_dir:
            dump_debug_page(driver, "login_error", debug_dir)
        return False
