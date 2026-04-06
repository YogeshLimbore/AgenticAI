"""
jobs/evaluator.py — LLM-powered job evaluation with Gemini 1.5 Flash (free).
Uses the cached LLMProvider so most repeat evaluations cost zero API calls.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from llm.provider import LLMProvider
from utils.logger import get_logger

log = get_logger("evaluator")

# Staffing / mass-recruiter firms — skip these automatically
KNOWN_BAD_RECRUITERS = [
    "staffing", "manpower", "quess corp", "teamlease",
    "mafoi", "ikya", "randstad",
]

# Hard stop phrases in any JD
HARD_STOP_PHRASES = ["bond", "service agreement", "security deposit", "pay to work"]

EVAL_SYSTEM = "You are a precise JSON-only job evaluator. Return ONLY valid JSON, no markdown."


def evaluate_job(
    job_title: str,
    company: str,
    job_description: str,
    your_skills: List[str],
    your_experience: str,
    llm: LLMProvider,
    today_plan: Optional[Dict] = None,
) -> Dict:
    """
    Score a job against the candidate profile using Gemini 1.5 Flash (free).
    Cached — identical JDs cost zero additional API calls.
    """
    plan_str = ""
    if today_plan:
        plan_str = (
            f"Focus keywords: {today_plan.get('focus_keywords', [])}\n"
            f"Skip if JD contains: {today_plan.get('skip_keywords', [])}\n"
            f"Minimum salary: {today_plan.get('min_salary', 0)} INR/year"
        )

    prompt = f"""Evaluate this job for the candidate.

=== CANDIDATE PROFILE ===
Skills: {', '.join(your_skills)}
Experience: {your_experience} years

=== JOB ===
Title: {job_title}
Company: {company}
Description:
{job_description[:2500]}

=== TODAY'S PLAN ===
{plan_str or "No special focus today."}

Return ONLY a JSON object with these keys:
{{
  "score": <integer 0-100>,
  "verdict": "<APPLY or SKIP>",
  "reason": "<1-2 sentence explanation>",
  "missing_skills": ["<skill1>", "<skill2>"],
  "jd_summary": "<1 sentence summary>",
  "salary_mentioned": "<salary range or 'Not mentioned'>",
  "red_flags": ["<concerning clause>"]
}}

Scoring: 80-100=perfect match, 60-79=good, 40-59=partial (skip), 0-39=poor (skip)."""

    # Cache key includes JD content → same JD won't be re-evaluated
    raw = llm.ask(prompt, system=EVAL_SYSTEM, use_cache=True)
    return _parse_evaluation(raw, job_title, company)


def _parse_evaluation(raw: str, title: str, company: str) -> Dict:
    default = {
        "score": 50,
        "verdict": "APPLY",
        "reason": "Could not evaluate (parse error). Applying by default.",
        "missing_skills": [],
        "jd_summary": f"{title} at {company}",
        "salary_mentioned": "Not mentioned",
        "red_flags": [],
    }
    if not raw:
        return default

    clean = re.sub(r"```(?:json)?|```", "", raw).strip()
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if not match:
        return default

    try:
        parsed = json.loads(match.group())
        parsed["score"] = int(parsed.get("score", 50))
        verdict = parsed.get("verdict", "APPLY").upper()
        parsed["verdict"] = verdict if verdict in ("APPLY", "SKIP") else "APPLY"
        return parsed
    except Exception:
        return default


def should_apply(
    evaluation: Dict,
    company: str,
    job_description: str,
    db,                              # Database instance
    today_plan: Optional[Dict] = None,
    threshold: int = 60,
) -> Tuple[bool, str]:
    """
    Multi-gate decision engine. Returns (go_ahead, reason).
    """
    # Gate 1: Blacklist
    if db.is_blacklisted(company):
        return False, f"Company '{company}' is blacklisted."

    # Gate 2: Known bad recruiters
    company_lower = company.strip().lower()
    for bad in KNOWN_BAD_RECRUITERS:
        if bad in company_lower:
            return False, f"Staffing/recruiter firm detected: '{company}'."

    # Gate 3: Score threshold
    score = evaluation.get("score", 0)
    if score < threshold:
        return False, f"Score {score} < threshold {threshold}."

    # Gate 4: LLM verdict
    if evaluation.get("verdict") == "SKIP":
        return False, f"LLM said SKIP: {evaluation.get('reason', '')}"

    # Gate 5: Today's skip keywords
    if today_plan:
        skip_kws = [k.lower() for k in today_plan.get("skip_keywords", [])]
        jd_lower = job_description.lower()
        for kw in skip_kws:
            if kw in jd_lower:
                return False, f"JD contains skip keyword: '{kw}'."

        # Gate 6: Minimum salary filter
        min_sal = today_plan.get("min_salary", 0)
        if min_sal and min_sal > 0:
            sal_text = evaluation.get("salary_mentioned", "")
            extracted = _extract_salary_lpa(sal_text)
            if extracted is not None and extracted < (min_sal / 100_000):
                return False, f"Salary {sal_text} below minimum {min_sal/100_000:.0f} LPA."

    # Gate 7: Hard-stop phrases in JD
    jd_lower = job_description.lower()
    for stop in HARD_STOP_PHRASES:
        if stop in jd_lower:
            return False, f"Hard red flag in JD: '{stop}'."

    return True, f"Score {score}/100 — {evaluation.get('reason', 'Good match')}"


def _extract_salary_lpa(text: str) -> Optional[float]:
    if not text:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if nums:
        return max(float(n) for n in nums)
    return None
