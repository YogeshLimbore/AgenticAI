"""
tests/test_evaluator.py — Unit tests for job_evaluator with mocked LLM
Run: pytest tests/ -v
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Mock LLM provider ─────────────────────────────────────────────────────

class MockLLM:
    def ask(self, prompt, system="", use_cache=True):
        return json.dumps({
            "score": 75,
            "verdict": "APPLY",
            "reason": "Good match for ML role",
            "missing_skills": ["Spark"],
            "jd_summary": "ML Engineer at TechCorp",
            "salary_mentioned": "8-12 LPA",
            "red_flags": [],
        })


# ── Evaluator tests ───────────────────────────────────────────────────────

class TestEvaluateJob:
    def test_returns_dict_with_score(self):
        from jobs.evaluator import evaluate_job
        llm = MockLLM()
        result = evaluate_job(
            job_title="ML Engineer",
            company="TechCorp",
            job_description="We need Python, ML, TensorFlow experience.",
            your_skills=["Python", "Machine Learning", "TensorFlow"],
            your_experience="1",
            llm=llm,
        )
        assert isinstance(result, dict)
        assert "score" in result
        assert 0 <= result["score"] <= 100

    def test_score_is_integer(self):
        from jobs.evaluator import evaluate_job
        result = evaluate_job("DS", "Co", "desc", ["Python"], "1", MockLLM())
        assert isinstance(result["score"], int)

    def test_verdict_is_apply_or_skip(self):
        from jobs.evaluator import evaluate_job
        result = evaluate_job("DS", "Co", "desc", ["Python"], "1", MockLLM())
        assert result["verdict"] in ("APPLY", "SKIP")

    def test_handles_empty_jd(self):
        from jobs.evaluator import evaluate_job
        result = evaluate_job("DS", "Co", "", ["Python"], "1", MockLLM())
        assert result is not None
        assert "score" in result

    def test_parse_bad_llm_response_returns_default(self):
        from jobs.evaluator import _parse_evaluation
        result = _parse_evaluation("not json at all", "Job", "Company")
        assert result["score"] == 50  # default
        assert result["verdict"] == "APPLY"

    def test_parse_llm_with_markdown_fences(self):
        from jobs.evaluator import _parse_evaluation
        raw = '```json\n{"score": 80, "verdict": "APPLY", "reason": "ok"}\n```'
        result = _parse_evaluation(raw, "Job", "Co")
        assert result["score"] == 80

    def test_salary_extraction(self):
        from jobs.evaluator import _extract_salary_lpa
        assert _extract_salary_lpa("8-12 LPA") == 12.0
        assert _extract_salary_lpa("6 LPA") == 6.0
        assert _extract_salary_lpa("Not mentioned") is None
        assert _extract_salary_lpa("") is None


class TestShouldApply:
    def setup_method(self):
        """Create a real in-memory SQLite DB for each test."""
        from storage.database import Database
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def teardown_method(self):
        self.tmp.cleanup()

    def test_apply_on_good_score(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 80, "verdict": "APPLY", "reason": "Good", "red_flags": []}
        ok, reason = should_apply(evaluation, "TechCorp", "Great ML role", self.db)
        assert ok is True

    def test_skip_below_threshold(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 40, "verdict": "APPLY", "reason": "Weak", "red_flags": []}
        ok, reason = should_apply(evaluation, "TechCorp", "role desc", self.db, threshold=60)
        assert ok is False
        assert "threshold" in reason.lower()

    def test_skip_blacklisted_company(self):
        from jobs.evaluator import should_apply
        self.db.blacklist_company("BadCorp")
        evaluation = {"score": 90, "verdict": "APPLY", "reason": "Great", "red_flags": []}
        ok, reason = should_apply(evaluation, "BadCorp", "desc", self.db)
        assert ok is False
        assert "blacklisted" in reason.lower()

    def test_skip_known_bad_recruiter(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 90, "verdict": "APPLY", "reason": "ok", "red_flags": []}
        ok, reason = should_apply(evaluation, "Manpower India", "desc", self.db)
        assert ok is False

    def test_skip_llm_verdict_skip(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 80, "verdict": "SKIP", "reason": "Bond required", "red_flags": []}
        ok, reason = should_apply(evaluation, "TechCorp", "desc", self.db)
        assert ok is False

    def test_skip_hard_stop_phrase_in_jd(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 85, "verdict": "APPLY", "reason": "ok", "red_flags": []}
        ok, reason = should_apply(evaluation, "TechCorp", "You must sign a service agreement", self.db)
        assert ok is False

    def test_skip_keyword_from_plan(self):
        from jobs.evaluator import should_apply
        evaluation = {"score": 80, "verdict": "APPLY", "reason": "ok", "red_flags": []}
        plan = {"skip_keywords": ["intern"], "min_salary": 0}
        ok, reason = should_apply(evaluation, "Co", "intern position ML", self.db, today_plan=plan)
        assert ok is False

    def test_skip_below_min_salary(self):
        from jobs.evaluator import should_apply
        evaluation = {
            "score": 80, "verdict": "APPLY", "reason": "ok",
            "red_flags": [], "salary_mentioned": "3-4 LPA"
        }
        plan = {"skip_keywords": [], "min_salary": 600_000}  # 6 LPA min
        ok, reason = should_apply(evaluation, "Co", "desc", self.db, today_plan=plan)
        assert ok is False


# ── Database tests ────────────────────────────────────────────────────────

class TestDatabase:
    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        from storage.database import Database
        self.db = Database(Path(self.tmp.name) / "test.db")

    def teardown_method(self):
        self.tmp.cleanup()

    def test_mark_applied_and_check(self):
        self.db.mark_applied("ML Engineer", "TechCorp", score=80, keyword="ML")
        assert self.db.is_already_applied("ML Engineer", "TechCorp") is True

    def test_not_applied_returns_false(self):
        assert self.db.is_already_applied("Unknown Job", "Unknown Co") is False

    def test_case_insensitive_applied_check(self):
        self.db.mark_applied("ml engineer", "techcorp")
        assert self.db.is_already_applied("ML Engineer", "TechCorp") is True

    def test_blacklist_and_check(self):
        self.db.blacklist_company("BadCorp", "rejected")
        assert self.db.is_blacklisted("BadCorp") is True
        assert self.db.is_blacklisted("GoodCorp") is False

    def test_blacklist_case_insensitive(self):
        self.db.blacklist_company("BadCorp")
        assert self.db.is_blacklisted("badcorp") is True
        assert self.db.is_blacklisted("BADCORP") is True

    def test_record_feedback_updates_stats(self):
        self.db.mark_applied("ML Eng", "Co", score=75)
        self.db.record_feedback("ML Eng", "Co", "interview", "Good call")
        stats = self.db.get_stats()
        assert stats["total_interviews"] == 1

    def test_invalid_outcome_rejected(self):
        self.db.record_feedback("Job", "Co", "invalid_outcome")
        stats = self.db.get_stats()
        assert stats["total_interviews"] == 0  # nothing recorded

    def test_today_plan_set_and_get(self):
        self.db.set_today_plan(
            focus_keywords=["NLP", "LLM"],
            skip_keywords=["intern"],
            min_salary=600_000,
            notes="Focus on NLP today"
        )
        plan = self.db.get_today_plan()
        assert plan is not None
        assert "NLP" in plan["focus_keywords"]
        assert plan["min_salary"] == 600_000

    def test_get_today_count(self):
        self.db.mark_applied("Job1", "Co1")
        self.db.mark_applied("Job2", "Co2")
        assert self.db.get_today_count() == 2

    def test_stats_increment_on_apply(self):
        before = self.db.get_stats()["total_applied"]
        self.db.mark_applied("Job", "Co")
        after = self.db.get_stats()["total_applied"]
        assert after == before + 1

    def test_compute_optimal_threshold_no_data(self):
        # With fewer than 20 outcomes, should return current threshold unchanged
        result = self.db.compute_optimal_threshold(60)
        assert result == 60

    def test_duplicate_apply_not_double_counted(self):
        self.db.mark_applied("Job", "Co")
        self.db.mark_applied("Job", "Co")  # duplicate
        assert self.db.get_today_count() == 1
