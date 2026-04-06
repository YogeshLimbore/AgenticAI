"""
analytics/insights.py — Derive actionable insights from historical application data.
Runs entirely from the local SQLite DB — zero API cost.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List

from storage.database import Database
from utils.logger import get_logger

log = get_logger("analytics")


def generate_insights(db: Database) -> Dict:
    """
    Return a dict of analytics insights from all historical data.
    Used in the daily summary and --analytics CLI command.
    """
    applied = db.get_applied_jobs(days=90)
    stats   = db.get_stats()

    if not applied:
        return {"message": "No data yet — run the agent first!"}

    # Keyword conversion rate
    keyword_outcomes: Dict[str, Dict] = {}
    for job in applied:
        kw = job.get("keyword", "unknown")
        if kw not in keyword_outcomes:
            keyword_outcomes[kw] = {"total": 0, "interviews": 0}
        keyword_outcomes[kw]["total"] += 1
        if job.get("outcome") == "interview":
            keyword_outcomes[kw]["interviews"] += 1

    keyword_rates = {
        kw: {
            "total": v["total"],
            "interviews": v["interviews"],
            "rate": f"{v['interviews']/v['total']*100:.0f}%",
        }
        for kw, v in sorted(
            keyword_outcomes.items(),
            key=lambda x: x[1]["interviews"] / max(1, x[1]["total"]),
            reverse=True,
        )
    }

    # Score distribution
    scores = [j.get("score", 0) for j in applied]
    avg_score = sum(scores) / len(scores) if scores else 0

    # Score vs outcome correlation
    interview_scores = [j.get("score", 0) for j in applied if j.get("outcome") == "interview"]
    rejected_scores  = [j.get("score", 0) for j in applied if j.get("outcome") == "rejected"]

    avg_interview_score = sum(interview_scores) / len(interview_scores) if interview_scores else 0
    avg_rejected_score  = sum(rejected_scores)  / len(rejected_scores)  if rejected_scores  else 0

    # Company response rates
    company_counter = Counter(j["company"] for j in applied if j.get("outcome") == "interview")
    top_responding_companies = company_counter.most_common(5)

    return {
        "total_applied":        stats.get("total_applied", 0),
        "total_interviews":     stats.get("total_interviews", 0),
        "total_rejections":     stats.get("total_rejections", 0),
        "total_no_response":    stats.get("total_no_response", 0),
        "total_offers":         stats.get("total_offers", 0),
        "overall_interview_rate": (
            f"{stats.get('total_interviews', 0) / max(1, stats.get('total_applied', 1)) * 100:.1f}%"
        ),
        "avg_application_score": f"{avg_score:.0f}",
        "avg_score_interviews":  f"{avg_interview_score:.0f}",
        "avg_score_rejections":  f"{avg_rejected_score:.0f}",
        "keyword_performance":   keyword_rates,
        "top_responding_companies": [
            {"company": c, "interviews": n}
            for c, n in top_responding_companies
        ],
        "score_insight": (
            f"You get interviews at avg score {avg_interview_score:.0f} "
            f"vs rejections at avg {avg_rejected_score:.0f}. "
            + (
                f"Consider {'lowering' if avg_interview_score < 65 else 'keeping'} "
                f"your threshold."
            )
            if interview_scores and rejected_scores else
            "Not enough outcome data yet for score insights."
        ),
    }


def print_analytics_report(db: Database):
    """Rich-formatted analytics report for --analytics CLI command."""
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    insights = generate_insights(db)

    if "message" in insights:
        console.print(f"\n[yellow]{insights['message']}[/]")
        return

    console.print("\n[bold]📊 APPLICATION ANALYTICS REPORT[/]")
    console.print("─" * 50)

    # Summary metrics
    console.print(f"  Total applied      : [bold]{insights['total_applied']}[/]")
    console.print(f"  Interviews         : [green]{insights['total_interviews']}[/]")
    console.print(f"  Rejections         : [red]{insights['total_rejections']}[/]")
    console.print(f"  No response        : [yellow]{insights['total_no_response']}[/]")
    console.print(f"  Offers             : [bold green]{insights['total_offers']}[/]")
    console.print(f"  Interview rate     : [bold]{insights['overall_interview_rate']}[/]")
    console.print(f"  Avg score (applied): {insights['avg_application_score']}/100")
    console.print(f"\n  💡 {insights['score_insight']}")

    # Keyword performance table
    kp = insights.get("keyword_performance", {})
    if kp:
        table = Table(title="\nKeyword Performance", box=box.SIMPLE)
        table.add_column("Keyword", style="cyan")
        table.add_column("Applied", justify="right")
        table.add_column("Interviews", justify="right")
        table.add_column("Rate", justify="right", style="green")
        for kw, data in kp.items():
            table.add_row(kw, str(data["total"]), str(data["interviews"]), data["rate"])
        console.print(table)

    # Top companies
    trc = insights.get("top_responding_companies", [])
    if trc:
        console.print("\n  🏢 Top responding companies:")
        for item in trc:
            console.print(f"     {item['company']}: {item['interviews']} interview(s)")

    console.print()
