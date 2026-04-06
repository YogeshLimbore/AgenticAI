"""
jobs/apply.py — Search and apply loop with retry logic and form filling.
"""

from __future__ import annotations

import datetime
import random
import time
from typing import Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.keys import Keys
from tenacity import retry, stop_after_attempt, wait_exponential

from browser.driver import (
    close_popups, dump_debug_page, first_element_from_selectors,
    first_text_from_selectors, human_sleep, normalize_text, safe_click,
)
from jobs.evaluator import evaluate_job, should_apply
from utils.logger import get_logger

log = get_logger("apply")


# ── URL builder ───────────────────────────────────────────────────────────

def build_search_url(keyword: str, location: str, exp: str) -> str:
    slug = keyword.lower().replace(" ", "-")
    k = keyword.replace(" ", "%20")
    l = location.replace(" ", "%20")
    return (
        f"https://www.naukri.com/{slug}-jobs"
        f"?k={k}&l={l}&experience={exp}&jobAge=1"
    )


# ── JD extraction ─────────────────────────────────────────────────────────

def extract_job_description(driver) -> str:
    selectors = [
        ".job-desc", "[class*='job-desc']", ".jobDescription",
        "#job-desc", ".jd-desc", ".description",
        "[class*='description']", ".dang-inner-html", "[class*='dang-inner']",
    ]
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            text = el.text.strip()
            if len(text) > 100:
                return text
        except Exception:
            continue
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        lines = [l.strip() for l in body.split("\n") if len(l.strip()) > 40]
        return "\n".join(lines[:80])
    except Exception:
        return ""


# ── Card parsing ──────────────────────────────────────────────────────────

def collect_job_cards(driver) -> List:
    selectors = [
        "[class*='srp-jobtuple-wrapper']", ".jobTuple",
        "article[data-job-id]", "article.jobTuple",
        ".cust-job-tuple", ".srp-jobtuple-wrapper",
    ]
    found, seen = [], set()
    for sel in selectors:
        try:
            for card in driver.find_elements(By.CSS_SELECTOR, sel):
                try:
                    key = (card.get_attribute("outerHTML") or "")[:200]
                    if key not in seen:
                        seen.add(key)
                        found.append(card)
                except StaleElementReferenceException:
                    continue
        except Exception:
            continue
    return found


def parse_job_card(card, fallback_keyword: str = "Role") -> Dict:
    title = first_text_from_selectors(card, [
        "a.title", ".title", "[title][class*='title']",
        "h2 a", "a[href*='/job-listings']",
    ]) or fallback_keyword
    company = first_text_from_selectors(card, [
        ".comp-name", ".companyName", ".companyInfo a",
        "a[href*='/company/']", ".subtitle",
    ]) or "Company"
    return {"title": title, "company": company}


def is_already_applied_ui(card) -> bool:
    try:
        txt = card.text.lower()
        return any(m in txt for m in ["already applied", "application sent", "applied"])
    except Exception:
        return False


def open_job_detail(driver, card) -> bool:
    current_handles = driver.window_handles[:]
    link = first_element_from_selectors(card, [
        "a.title", ".title a", "h2 a",
        "a[href*='/job-listings']", "a",
    ])
    if not link:
        return False
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", link
        )
        human_sleep(0.4, 0.9)
        driver.execute_script("arguments[0].click();", link)
        human_sleep(3, 5)
        new_handles = driver.window_handles
        if len(new_handles) > len(current_handles):
            driver.switch_to.window(new_handles[-1])
        return True
    except Exception:
        return False


# ── Apply logic ───────────────────────────────────────────────────────────

def application_success_detected(driver) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(x in body for x in [
            "already applied", "application submitted",
            "successfully applied", "you have successfully applied",
            "applied successfully",
        ])
    except Exception:
        return False


def current_page_has_form(driver) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
        return any(x in body for x in [
            "current ctc", "expected ctc", "notice period",
            "submit", "continue", "next", "screening questions",
        ])
    except Exception:
        return False


def _ancestors(el, n):
    result = []
    for i in range(1, n + 1):
        try:
            result.append(el.find_element(By.XPATH, f"./ancestor::*[{i}]"))
        except Exception:
            pass
    return result


def find_nearby_field(driver, keywords: List[str]):
    kw_expr = " or ".join([
        f"contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        f"'abcdefghijklmnopqrstuvwxyz'), '{kw.lower()}')"
        for kw in keywords
    ])
    label_xpath = f"//*[self::label or self::span or self::div or self::p][{kw_expr}]"
    try:
        candidates = driver.find_elements(By.XPATH, label_xpath)
    except Exception:
        candidates = []
    for label in candidates:
        for container in [label] + _ancestors(label, 2):
            try:
                fields = container.find_elements(
                    By.CSS_SELECTOR,
                    "input, textarea, select, [role='combobox'], [contenteditable='true']",
                )
                for field in fields:
                    if field.is_displayed() and field.is_enabled():
                        return field, container
            except Exception:
                continue
    return None, None


def fill_application_form(driver, defaults: Dict) -> bool:
    filled = False
    mapping = [
        (["current ctc", "current salary"],          defaults.get("current_ctc", "4")),
        (["expected ctc", "expected salary"],         defaults.get("expected_ctc", "6")),
        (["notice period"],                           defaults.get("notice_period", "30")),
        (["total experience", "years of experience"], defaults.get("total_experience", "1")),
        (["current location", "current city"],        defaults.get("current_location", "Pune")),
    ]
    for keywords, value in mapping:
        field, container = find_nearby_field(driver, keywords)
        if field is not None:
            tag = (field.tag_name or "").lower()
            if tag == "select":
                try:
                    Select(field).select_by_visible_text(str(value))
                    filled = True
                except Exception:
                    pass
            else:
                try:
                    field.click()
                    field.clear()
                    field.send_keys(Keys.CONTROL, "a")
                    field.send_keys(Keys.DELETE)
                    field.send_keys(str(value))
                    filled = True
                except Exception:
                    pass

    # Handle willing_to_relocate — it's usually a radio button or dropdown,
    # not a plain text field, so we look for the container and click the option.
    relocate_value = defaults.get("willing_to_relocate", "Yes")
    relocate_field, relocate_container = find_nearby_field(
        driver, ["willing to relocate", "relocate", "relocation"]
    )
    if relocate_container:
        desired_norm = normalize_text(relocate_value)
        try:
            for opt in relocate_container.find_elements(
                By.CSS_SELECTOR, "label, button, span, div, li, a, input[type='radio']"
            ):
                if normalize_text(opt.text) == desired_norm:
                    safe_click(driver, opt)
                    filled = True
                    break
        except Exception:
            pass
    elif relocate_field:
        # Fallback: it was a plain input
        try:
            relocate_field.click()
            relocate_field.clear()
            relocate_field.send_keys(relocate_value)
            filled = True
        except Exception:
            pass

    # Submit
    for sel in ["button[type='submit']", "input[type='submit']",
                "button.submit", "#submit-button"]:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed() and btn.is_enabled():
                safe_click(driver, btn)
                human_sleep(2, 3)
                break
        except Exception:
            continue
    return filled


def click_apply_button(driver) -> bool:
    for sel in ["#apply-button", "button.apply-button"]:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                btn_id    = normalize_text(btn.get_attribute("id") or "")
                btn_class = normalize_text(btn.get_attribute("class") or "")
                if "company-site-button" in (btn_id + btn_class):
                    continue
                if normalize_text(btn.text) == "apply":
                    if safe_click(driver, btn):
                        time.sleep(2)
                        return True
        except Exception:
            continue
    # Fallback: any "apply" button
    try:
        for btn in driver.find_elements(By.CSS_SELECTOR, "button"):
            if normalize_text(btn.text) == "apply":
                if "company-site-button" not in normalize_text(
                    (btn.get_attribute("id") or "") + (btn.get_attribute("class") or "")
                ):
                    if safe_click(driver, btn):
                        time.sleep(2)
                        return True
    except Exception:
        pass
    return False


# ── Main search & apply loop ──────────────────────────────────────────────

def search_and_apply(
    driver,
    settings,
    db,
    llm,
    today_plan: Optional[Dict],
    console_ui,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Main loop: search all keywords → evaluate each job → apply if worthy.
    Returns (applied_jobs, skipped_jobs).
    """
    applied_jobs: List[Dict] = []
    skipped_jobs: List[Dict] = []
    applied_count = 0

    # Prioritize today's focus keywords
    keywords_to_search = list(settings.job_keywords)
    if today_plan and today_plan.get("focus_keywords"):
        focus = [k for k in today_plan["focus_keywords"]
                 if k not in keywords_to_search]
        keywords_to_search = focus + keywords_to_search

    # Adaptive threshold from feedback history
    threshold = db.compute_optimal_threshold(settings.match_threshold)

    for keyword in keywords_to_search:
        if applied_count >= settings.max_apply_per_run:
            break

        log.info(f"Searching: '{keyword}' in {settings.job_location}")
        url = build_search_url(keyword, settings.job_location, settings.experience_yrs)

        try:
            driver.get(url)
        except Exception as e:
            log.warning(f"Page load failed for '{keyword}': {e}")
            continue

        human_sleep(4, 6)
        close_popups(driver)

        # Wait for job cards
        cards = []
        for _ in range(15):
            close_popups(driver)
            cards = collect_job_cards(driver)
            if cards:
                break
            driver.execute_script("window.scrollBy(0, 800);")
            human_sleep(0.8, 1.4)

        log.info(f"Found {len(cards)} listings for '{keyword}'")
        if not cards:
            dump_debug_page(driver, f"no_jobs_{keyword.replace(' ', '_')}",
                            settings.debug_dir)
            continue

        for idx, card in enumerate(cards[:12], start=1):
            if applied_count >= settings.max_apply_per_run:
                break

            base_handle = driver.current_window_handle

            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", card
                )
                human_sleep(1, 1.5)
                close_popups(driver)

                parsed  = parse_job_card(card, keyword)
                title   = parsed["title"]
                company = parsed["company"]

                # ── Gate: UI already-applied marker ──
                if is_already_applied_ui(card):
                    log.debug(f"UI says already applied: {title} @ {company}")
                    continue

                # ── Gate: Our DB applied check ────────
                if db.is_already_applied(title, company):
                    log.debug(f"DB: already applied to {title} @ {company}")
                    continue

                # ── Open detail page ──────────────────
                opened = open_job_detail(driver, card)
                if opened:
                    close_popups(driver)
                    human_sleep(2, 3)

                # ── LLM Evaluation ────────────────────
                jd = extract_job_description(driver) if opened else ""
                evaluation = evaluate_job(
                    job_title=title, company=company,
                    job_description=jd,
                    your_skills=settings.your_skills,
                    your_experience=settings.experience_yrs,
                    llm=llm,
                    today_plan=today_plan,
                )
                score  = evaluation.get("score", 0)
                reason = evaluation.get("reason", "")
                log.info(f"Score: {score}/100 — {reason[:70]}")

                # ── Decision engine ───────────────────
                go_ahead, decision_reason = should_apply(
                    evaluation=evaluation, company=company,
                    job_description=jd, db=db,
                    today_plan=today_plan, threshold=threshold,
                )
                if not go_ahead:
                    log.info(f"Skipping: {decision_reason}")
                    skipped_jobs.append({
                        "title": title, "company": company,
                        "score": score, "reason": decision_reason,
                    })
                    continue

                # ── Apply ─────────────────────────────
                applied = False
                # Try card-level apply button
                try:
                    for btn in card.find_elements(By.CSS_SELECTOR,
                                                  "button, a, [role='button'], span"):
                        txt    = normalize_text(btn.text)
                        btn_id = normalize_text(btn.get_attribute("id") or "")
                        btn_cls= normalize_text(btn.get_attribute("class") or "")
                        if "company-site-button" in (btn_id + btn_cls):
                            continue
                        if txt == "apply" or btn_id == "apply-button":
                            if safe_click(driver, btn):
                                human_sleep(2, 3)
                                if application_success_detected(driver):
                                    applied = True
                                elif current_page_has_form(driver):
                                    applied = fill_application_form(
                                        driver,
                                        {
                                            "current_ctc":    settings.current_ctc,
                                            "expected_ctc":   settings.expected_ctc,
                                            "notice_period":  settings.notice_period,
                                            "total_experience": settings.total_experience,
                                            "current_location": settings.current_location,
                                        }
                                    )
                                else:
                                    applied = True
                                break
                except Exception:
                    pass

                # Try detail page apply button
                if not applied and opened:
                    if click_apply_button(driver):
                        human_sleep(2, 4)
                        if application_success_detected(driver):
                            applied = True
                        elif current_page_has_form(driver):
                            applied = fill_application_form(driver, {
                                "current_ctc":    settings.current_ctc,
                                "expected_ctc":   settings.expected_ctc,
                                "notice_period":  settings.notice_period,
                                "total_experience": settings.total_experience,
                                "current_location": settings.current_location,
                            })

                if application_success_detected(driver):
                    applied = True

                if applied:
                    db.mark_applied(
                        title=title, company=company, score=score,
                        keyword=keyword,
                        jd_summary=evaluation.get("jd_summary", ""),
                    )
                    applied_count += 1
                    job_record = {
                        "title": title, "company": company,
                        "keyword": keyword, "score": score,
                        "jd_summary": evaluation.get("jd_summary", ""),
                        "missing_skills": evaluation.get("missing_skills", []),
                        "time": datetime.datetime.now().strftime("%H:%M"),
                    }
                    applied_jobs.append(job_record)
                    log.info(
                        f"[green]Applied ({applied_count}/{settings.max_apply_per_run}): "
                        f"{title} @ {company} [score {score}][/]"
                    )
                    human_sleep(2, 4)
                else:
                    log.warning(f"No usable apply path: {title} @ {company}")

            except Exception as e:
                log.warning(f"Card {idx} failed: {e}")

            finally:
                # Close extra tabs
                try:
                    handles = driver.window_handles
                    if len(handles) > 1:
                        for h in handles:
                            if h != base_handle:
                                driver.switch_to.window(h)
                                driver.close()
                        driver.switch_to.window(base_handle)
                except Exception:
                    pass

    log.info(f"Session complete — Applied: {applied_count} | Skipped: {len(skipped_jobs)}")
    return applied_jobs, skipped_jobs
