"""
main.py — Naukri AI Agent v2 entry point
Features:
  - Rich live dashboard during runs
  - Graceful shutdown (SIGINT/SIGTERM)
  - APScheduler for automated daily runs
  - All CLI commands: run, feedback, plan, blacklist, analytics, memory, schedule
"""

from __future__ import annotations

import datetime
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# ── Rich UI ───────────────────────────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.layout import Layout
from rich.text import Text
from rich import box

console = Console()

# ── Project modules ───────────────────────────────────────────────────────
from config.settings import get_settings
from utils.logger import setup_logging, get_logger
from utils.credentials import get_credentials
from storage.database import Database
from llm.provider import LLMProvider
from browser.driver import create_driver, clear_session_cookies
from auth.login import login
from jobs.apply import search_and_apply
from analytics.insights import generate_insights, print_analytics_report
from notifications.telegram import send_daily_summary

log = get_logger("main")

# ── Global driver reference for graceful shutdown ─────────────────────────
_driver = None
_shutdown_requested = False


def _signal_handler(sig, frame):
    global _shutdown_requested
    console.print("\n[yellow]⚠️  Shutdown signal received — cleaning up...[/]")
    _shutdown_requested = True
    if _driver:
        try:
            _driver.quit()
            log.info("Chrome driver closed cleanly")
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGINT,  _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── Rich dashboard helpers ────────────────────────────────────────────────

def print_header():
    console.print(Panel(
        Text.assemble(
            ("🤖 NAUKRI AI AGENT v2\n", "bold cyan"),
            (f"📅 {datetime.datetime.now().strftime('%A, %B %d %Y  %H:%M')}\n", "dim"),
            ("Free tier: Gemini 1.5 Flash · SQLite memory · Telegram alerts", "dim"),
        ),
        border_style="cyan",
    ))


def print_memory_overview(db: Database):
    stats = db.get_stats()
    today = db.get_today_count()
    plan  = db.get_today_plan()
    blacklist = db.get_blacklist()

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Key", style="dim")
    table.add_column("Value", style="bold")
    table.add_row("Applied today",    str(today))
    table.add_row("Total applied",    str(stats.get("total_applied", 0)))
    table.add_row("Interviews",       str(stats.get("total_interviews", 0)))
    table.add_row("Rejections",       str(stats.get("total_rejections", 0)))
    table.add_row("Offers",           str(stats.get("total_offers", 0)))
    table.add_row("Blacklisted cos",  str(len(blacklist)))
    if plan:
        table.add_row("Today's focus", str(plan.get("notes") or plan.get("focus_keywords")))

    console.print(Panel(table, title="📊 Agent Memory", border_style="blue"))


def print_summary(applied: list, skipped: list, profile_updates: dict,
                  llm_usage: dict, db: Database):
    stats = db.get_stats()
    avg_score = (
        sum(j.get("score", 0) for j in applied) / len(applied)
        if applied else 0
    )

    console.print("\n" + "═" * 60)
    console.print("[bold cyan]  📊 DAILY SUMMARY[/]")
    console.print("═" * 60)
    console.print(f"  ✅ Applied    : [bold green]{len(applied)}[/] jobs (avg score {avg_score:.0f}/100)")
    console.print(f"  ❌ Skipped   : [yellow]{len(skipped)}[/] (low score / filtered)")
    console.print(f"  💰 LLM calls : {llm_usage.get('api_calls', 0)} API + "
                  f"[green]{llm_usage.get('cache_hits', 0)} cached[/] "
                  f"(saved ~{llm_usage.get('cache_hits', 0) * 0.001:.3f}$ in API costs)")
    console.print(f"  🎯 All-time  : {stats.get('total_applied', 0)} applied · "
                  f"{stats.get('total_interviews', 0)} interviews · "
                  f"{stats.get('total_offers', 0)} offers")

    if applied:
        console.print("\n  [bold]Jobs applied to:[/]")
        for job in applied[:10]:
            console.print(
                f"    [green]✓[/] {job['title']} @ [cyan]{job['company']}[/] "
                f"[dim][score {job.get('score', 0)}][/]"
            )
        if len(applied) > 10:
            console.print(f"    ... and {len(applied) - 10} more")

    if skipped[:3]:
        console.print("\n  [bold]Top skipped:[/]")
        for job in skipped[:3]:
            console.print(
                f"    [red]✗[/] {job['title']} @ {job['company']} "
                f"[dim]— {job.get('reason', '')[:60]}[/]"
            )

    console.print("═" * 60)


def generate_and_save_summary(applied: list, skipped: list,
                               llm, db: Database, settings) -> str:
    """Generate AI summary using Gemini (cached if same data)."""
    stats = db.get_stats()
    avg_score = sum(j.get("score", 0) for j in applied) / max(1, len(applied))

    data = {
        "date": datetime.date.today().strftime("%B %d, %Y"),
        "jobs_applied": len(applied),
        "jobs_skipped": len(skipped),
        "avg_score": avg_score,
        "top_jobs": applied[:5],
        "stats": stats,
    }

    prompt = f"""Write a short, friendly daily activity summary for a smart job seeker.

Data:
{json.dumps(data, indent=2)}

Include: date and counts, quality of applications (avg score), what was skipped briefly,
short motivational closing. Keep under 200 words. Be encouraging."""

    summary = llm.ask(prompt, system="You are a helpful job search assistant.", use_cache=False)

    if not summary:
        summary = (
            f"📅 {data['date']}\n\n"
            f"Applied to {data['jobs_applied']} jobs (avg score {avg_score:.0f}/100).\n"
            f"Skipped {data['jobs_skipped']} low-match jobs.\n"
            f"All-time: {stats.get('total_applied', 0)} applied, "
            f"{stats.get('total_interviews', 0)} interviews.\n\n"
            "Keep going — consistent action compounds!"
        )

    today_str = datetime.date.today().strftime("%Y-%m-%d")
    log_file  = settings.log_dir / f"summary_{today_str}.txt"
    log_file.write_text(summary, encoding="utf-8")
    log.info(f"Summary saved: {log_file}")
    return summary


def update_profile(driver, settings, llm):
    """Rotate profile headline and suggest new skills."""
    log.info("Updating Naukri profile...")
    result = {"headline_updated": False, "new_headline": ""}

    try:
        driver.get("https://www.naukri.com/mnjuser/profile")
        time.sleep(3)

        headlines = [
            "Data Scientist | ML & NLP Enthusiast | Python Expert",
            "AI/ML Engineer | Deep Learning | PyTorch & TensorFlow",
            "Data Science Professional | Computer Vision | Python",
            "Machine Learning Engineer | NLP | Data-Driven Solutions",
        ]
        new_headline = headlines[datetime.date.today().day % len(headlines)]
        result["new_headline"] = new_headline

        from browser.driver import close_popups, safe_click
        close_popups(driver)

        try:
            from selenium.webdriver.common.by import By
            headline_el = driver.find_element(
                By.CSS_SELECTOR,
                "[class*='headline'], [placeholder*='headline'], #headline"
            )
            safe_click(driver, headline_el)
            time.sleep(1)
            edit_btn = driver.find_element(By.CSS_SELECTOR, ".editIcon, .edit-icon, button.edit")
            safe_click(driver, edit_btn)
            time.sleep(1)
            input_el = driver.find_element(
                By.CSS_SELECTOR,
                "input[placeholder*='headline'], textarea[placeholder*='headline']"
            )
            input_el.clear()
            input_el.send_keys(new_headline)
            time.sleep(0.5)
            save_btn = driver.find_element(
                By.CSS_SELECTOR, "button[type='submit'], .saveBtn, .save-btn"
            )
            safe_click(driver, save_btn)
            time.sleep(2)
            result["headline_updated"] = True
            log.info(f"Headline updated: {new_headline}")
        except Exception:
            log.debug("Could not update headline (UI may have changed)")

    except Exception as e:
        log.warning(f"Profile update failed: {e}")

    return result


# ── Core run function ─────────────────────────────────────────────────────

def run_agent():
    global _driver

    settings = get_settings()
    setup_logging(settings.log_dir)

    print_header()

    # Validate config
    missing = settings.validate_required()
    if missing:
        console.print(f"[bold red]❌ Missing required config: {', '.join(missing)}[/]")
        console.print("   Copy .env.template to .env and fill in your values.")
        return

    # Init services
    db  = Database(settings.db_path)
    llm = LLMProvider(
        api_key=settings.gemini_api_key,
        cache_path=settings.llm_cache_path,
        model=settings.gemini_model,
        cache_ttl_hours=settings.llm_cache_ttl_hours,
    )

    # Secure credential fetch
    email, password = get_credentials(
        store=settings.credential_store,
        email=settings.naukri_email,
        password=settings.naukri_password,
    )

    print_memory_overview(db)

    # Today's plan
    today_plan = db.get_today_plan()
    if today_plan:
        console.print(
            f"\n[cyan]📋 Today's plan:[/] {today_plan.get('notes') or today_plan.get('focus_keywords')}"
        )

    # Create browser
    console.print("\n[dim]Starting Chrome...[/]")
    _driver = create_driver()

    try:
        # Login
        console.print("\n[bold][1/3] 🔐 Logging in...[/]")
        if not login(_driver, email, password, debug_dir=settings.debug_dir):
            console.print("[bold red]❌ Login failed. Check your credentials.[/]")
            return

        # Search & apply
        console.print("\n[bold][2/3] 🔍 Searching and applying...[/]")
        applied_jobs, skipped_jobs = search_and_apply(
            driver=_driver,
            settings=settings,
            db=db,
            llm=llm,
            today_plan=today_plan,
            console_ui=console,
        )

        # Update profile
        console.print("\n[bold][3/3] 🧠 Updating profile...[/]")
        profile_updates = update_profile(_driver, settings, llm)

        # Summary
        llm_usage = llm.usage_summary()
        print_summary(applied_jobs, skipped_jobs, profile_updates, llm_usage, db)

        # AI-generated summary
        summary = generate_and_save_summary(applied_jobs, skipped_jobs, llm, db, settings)

        # Telegram notification
        if settings.telegram_bot_token and settings.telegram_chat_id:
            console.print("\n[dim]Sending Telegram notification...[/]")
            sent = send_daily_summary(
                token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                applied=applied_jobs,
                skipped=skipped_jobs,
                stats=db.get_stats(),
                llm_usage=llm_usage,
            )
            if sent:
                console.print("[green]✓ Telegram summary sent[/]")

        console.print("\n[bold green]✅ Agent finished successfully.[/]")

    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        console.print(f"\n[bold red]❌ Agent error: {e}[/]")

    finally:
        if _driver:
            time.sleep(1)
            _driver.quit()
            _driver = None


# ── CLI commands ──────────────────────────────────────────────────────────

def cmd_feedback():
    """Interactive feedback recorder."""
    settings = get_settings()
    setup_logging(settings.log_dir)
    db = Database(settings.db_path)

    console.print("\n[bold]🔄 FEEDBACK LOOP — Record job outcome[/]\n")
    title   = input("Job title  : ").strip()
    company = input("Company    : ").strip()

    console.print("\nOutcome options:")
    console.print("  1. interview    — Got a call/interview")
    console.print("  2. rejected     — Got a rejection email")
    console.print("  3. no_response  — Heard nothing")
    console.print("  4. offer        — Got an offer! 🎉")

    choice_map = {"1": "interview", "2": "rejected", "3": "no_response", "4": "offer"}
    choice  = input("\nEnter 1-4 : ").strip()
    outcome = choice_map.get(choice, "no_response")
    notes   = input("Notes (optional): ").strip()

    db.record_feedback(title, company, outcome, notes)
    console.print(f"[green]✓ Recorded: {title} @ {company} → {outcome}[/]")

    if outcome == "rejected":
        bl = input(f"\nBlacklist '{company}'? (y/n): ").strip().lower()
        if bl == "y":
            reason = input("Reason: ").strip()
            db.blacklist_company(company, reason)
            console.print(f"[red]🚫 Blacklisted: {company}[/]")

    # Auto-tune threshold after recording feedback
    current = get_settings().match_threshold
    new     = db.compute_optimal_threshold(current)
    if new != current:
        console.print(
            f"\n[yellow]💡 Threshold suggestion: {current} → {new} "
            f"(based on your interview rate). "
            f"Update MATCH_THRESHOLD in .env to apply.[/]"
        )


def cmd_plan():
    """Set today's search plan."""
    settings = get_settings()
    setup_logging(settings.log_dir)
    db = Database(settings.db_path)

    console.print("\n[bold]🗺️  SET TODAY'S PLAN[/]\n")
    focus  = input("Focus keywords (comma-separated, or Enter to skip): ").strip()
    skip   = input("Skip keywords  (comma-separated, or Enter to skip): ").strip()
    salary = input("Min salary LPA (number, or Enter for no filter)   : ").strip()
    notes  = input("Goal / notes for today                            : ").strip()

    db.set_today_plan(
        focus_keywords=[f.strip() for f in focus.split(",") if f.strip()],
        skip_keywords =[s.strip() for s in skip.split(",")  if s.strip()],
        min_salary    =int(float(salary) * 100_000) if salary else 0,
        notes         =notes,
    )
    console.print("[green]✓ Plan saved. Run the agent to start.[/]")


def cmd_blacklist():
    """Add a company to blacklist."""
    settings = get_settings()
    setup_logging(settings.log_dir)
    db = Database(settings.db_path)

    company = input("Company to blacklist: ").strip()
    reason  = input("Reason (optional)  : ").strip()
    db.blacklist_company(company, reason)


def cmd_memory():
    """Show memory overview."""
    settings = get_settings()
    setup_logging(settings.log_dir)
    db = Database(settings.db_path)
    print_memory_overview(db)


def cmd_analytics():
    """Show detailed analytics report."""
    settings = get_settings()
    setup_logging(settings.log_dir)
    db = Database(settings.db_path)
    print_analytics_report(db)


def cmd_schedule():
    """Start the APScheduler daemon for automatic daily runs."""
    settings = get_settings()
    setup_logging(settings.log_dir)

    if not settings.schedule_time:
        console.print("[yellow]SCHEDULE_TIME not set in .env (e.g. SCHEDULE_TIME=09:00)[/]")
        return

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        console.print("[red]APScheduler not installed. Run: pip install APScheduler[/]")
        return

    hour, minute = map(int, settings.schedule_time.split(":"))
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_agent, "cron",
        hour=hour, minute=minute,
        id="daily_run",
    )

    console.print(
        f"[green]✓ Scheduler started — will run daily at "
        f"[bold]{settings.schedule_time}[/] (Ctrl+C to stop)[/]"
    )
    console.print("[dim]Tip: Run this in a tmux/screen session so it stays active.[/]")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Scheduler stopped.[/]")


def cmd_clear_session():
    """Clear saved session cookies (force re-login next run)."""
    clear_session_cookies()
    console.print("[green]✓ Session cookies cleared — will re-login next run.[/]")


def cmd_store_credentials():
    """Store credentials in OS keyring (more secure than .env)."""
    from utils.credentials import store_credentials_in_keyring
    email    = input("Naukri email   : ").strip()
    password = input("Naukri password: ").strip()
    if store_credentials_in_keyring(email, password):
        console.print(
            "[green]✓ Credentials stored in OS keyring.\n"
            "  Set CREDENTIAL_STORE=keyring in your .env[/]"
        )


# ── CLI dispatcher ────────────────────────────────────────────────────────

COMMANDS = {
    "--feedback":          (cmd_feedback,          "Log interview/rejection outcome"),
    "--plan":              (cmd_plan,               "Set today's job search focus"),
    "--blacklist":         (cmd_blacklist,          "Blacklist a company"),
    "--memory":            (cmd_memory,             "Show memory & stats overview"),
    "--analytics":         (cmd_analytics,          "Full analytics report"),
    "--schedule":          (cmd_schedule,           "Start automated daily scheduler"),
    "--clear-session":     (cmd_clear_session,      "Force re-login on next run"),
    "--store-credentials": (cmd_store_credentials,  "Save credentials to OS keyring"),
}


def print_help():
    console.print("\n[bold]Naukri AI Agent v2[/] — usage:\n")
    console.print("  [cyan]python main.py[/]                   Run the agent now")
    for flag, (_, desc) in COMMANDS.items():
        console.print(f"  [cyan]python main.py {flag:<22}[/] {desc}")
    console.print()


def cli():
    if len(sys.argv) < 2:
        run_agent()
        return

    flag = sys.argv[1]

    if flag in ("--help", "-h"):
        print_help()
        return

    if flag in COMMANDS:
        fn, _ = COMMANDS[flag]
        fn()
    else:
        console.print(f"[red]Unknown command: {flag}[/]")
        print_help()


if __name__ == "__main__":
    cli()
