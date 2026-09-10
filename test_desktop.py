from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

CODE_ROOT = Path(__file__).resolve().parents[1]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from desktop.backend.run_control import request_soft_stop, validate_batch_bounds  # noqa: E402
from desktop.backend.scaffold import ensure_workspace, inspect_workspace  # noqa: E402
from desktop.backend.runner import produce_batch, qw_json  # noqa: E402
from desktop.backend import runner as desktop_runner  # noqa: E402


def _qa_payload() -> dict:
    return {
        "question_type": "short_answer",
        "question": "When the two sources' operative rules conflict, which wording should the system treat as final?",
        "answer": "cross-document synthesis",
        "evidence": [
            {"id": "E1", "quote": "The first document establishes the initial constraint.", "location": "DOC 1 / section A"},
            {"id": "E2", "quote": "The second document reverses the initial constraint.", "location": "DOC 2 / section B"},
            {"id": "E3", "quote": "The third document binds the reversed constraint to the decision phrase.", "location": "DOC 2 / section C"},
        ],
        "reasoning": "E1 establishes the initial branch; E2 reverses it; E3 binds the reversed constraint to the decision phrase, so the cross-document synthesis is selected.",
        "confusing_facts": [
            {"quote": "The nearby document preserves the initial constraint.", "location": "DOC 2", "why_wrong": "near-miss that never reverses the constraint"},
            {"quote": "The later notice restates the initial constraint without reversal.", "location": "DOC 2", "why_wrong": "repeats the stale constraint"},
        ],
        "wrong_answers": ["initial constraint"],
        "difficulty_tags": {
            "strategy_version": "v4.0",
            "primary_knob": "cfi",
            "secondary_knobs": ["hop", "sem", "mix", "dens", "gate"],
            "hops": 3,
            "semantic_distance_note": "question uses 约束/决策 rather than quote nouns",
            "cfi_enabled": True,
            "cue_bound_cfi": True,
            "kpr_applied": False,
        },
        "design_rationale": "The result depends on distant evidence and a non-trivial branch.",
        "solution_steps": ["compare E1", "apply E2", "bind E3"],
    }


def _context() -> str:
    return (
        "=== D01: first ===\n"
        + "a " * 7_000
        + "The first document establishes the initial constraint.\n"
        + "a " * 1_000
        + "\n=== D02: second ===\n"
        + "b " * 7_000
        + "The second document reverses the initial constraint.\n"
        + "The nearby document preserves the initial constraint.\n"
        + "The later notice restates the initial constraint without reversal.\n"
        + "b " * 2_000
        + "The third document binds the reversed constraint to the decision phrase.\n"
        + "b " * 500
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_fixture(root: Path, *, correct_count: int = 2) -> None:
    qa = _qa_payload()
    base = root / "candidate_01"
    _write_json(base / "generate_qa.json", {"content": json.dumps(qa, ensure_ascii=False), "api_key": "sk-secret-value"})
    for run_id in range(1, 9):
        _write_json(
            base / f"rollout_{run_id}.json",
            {
                "content": f"answer {run_id}",
                "finish_reason": "stop",
                "params": {"temperature": 0.7, "top_p": 0.8, "max_tokens": 16384, "enable_thinking": True},
                "authorization": "Bearer secret-value",
            },
        )
        _write_json(
            base / f"judge_{run_id}.json",
            {"content": json.dumps({"correct": run_id <= correct_count, "extracted_answer": "cross-document synthesis", "reason": "fixture"})},
        )
    for mode, correct in (("none", False), ("truncated", False), ("full", True)):
        _write_json(base / f"ablation_{mode}.json", {"content": f"{mode} answer", "finish_reason": "stop"})
        _write_json(
            base / f"ablation_judge_{mode}.json",
            {"content": json.dumps({"correct": correct, "extracted_answer": "cross-document synthesis", "reason": "fixture"})},
        )


class DesktopScaffoldTests(unittest.TestCase):
    def test_existing_repo_skips_scaffold_copy(self) -> None:
        info = inspect_workspace(CODE_ROOT)
        self.assertIn(info["status"], {"ready", "legacy_ready"})
        self.assertTrue(info["existing_repo"])
        result = ensure_workspace(CODE_ROOT)
        self.assertFalse(result.get("copied_pipeline"))
        self.assertTrue((CODE_ROOT / ".lcqa" / "workspace.json").is_file())
        self.assertTrue((CODE_ROOT / "materials").is_dir())

    def test_empty_dir_gets_scaffold_and_pipeline_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            root.mkdir()
            result = ensure_workspace(root)
            self.assertTrue(result["copied_pipeline"])
            self.assertTrue((root / "materials" / "README.md").is_file())
            self.assertTrue((root / "materials" / "example" / "sample-pack" / "CATALOG.json").is_file())
            self.assertTrue((root / "scripts" / "produce_one.py").is_file())
            self.assertTrue((root / "scripts" / "lcqa_reasoning.py").is_file())
            self.assertTrue((root / "workflow" / "batch_run.py").is_file())
            self.assertTrue((root / "queue" / "queue.json").is_file())
            self.assertTrue((root / "archive" / "pending-review").is_dir())
            self.assertIn("file_md", (root / "materials" / "README.md").read_text(encoding="utf-8"))
            again = ensure_workspace(root)
            self.assertFalse(again.get("copied_pipeline"))


class DesktopBoundsTests(unittest.TestCase):
    def test_limit_and_workers_rejected_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            validate_batch_bounds(0, 1)
        with self.assertRaises(ValueError):
            validate_batch_bounds(1, 0)
        with self.assertRaises(ValueError):
            validate_batch_bounds(1, 11)
        self.assertEqual(validate_batch_bounds(200, 10), (200, 10))
        self.assertEqual(validate_batch_bounds(500, 1), (500, 1))


class DesktopRunControlTests(unittest.TestCase):
    def test_soft_stop_releases_and_resume_finishes_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            root.mkdir()
            ensure_workspace(root)
            fixture = root / "fixtures"
            _make_fixture(fixture)
            staging = root / "data" / "staging" / "demo"
            staging.mkdir(parents=True)
            (staging / "context.txt").write_text(_context(), encoding="utf-8")
            (staging / "context.md").write_text(_context(), encoding="utf-8")
            _write_json(staging / "sources.json", {"domain": "test", "docs": [{"doc_id": "D01"}, {"doc_id": "D02"}]})
            _write_json(
                staging / "meta.json",
                {"slug": "demo", "domain": "example", "materials_pack": "materials/example/sample-pack"},
            )
            qw_json(
                root,
                [
                    "enqueue",
                    "--slug",
                    "demo",
                    "--domain",
                    "example",
                    "--staging",
                    "data/staging/demo",
                    "--materials-pack",
                    "materials/example/sample-pack",
                    "--provider-mode",
                    "fixture",
                    "--include-used",
                ],
            )
            queue = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["tasks"][0]["status"], "queued")

            started = threading.Event()
            original = desktop_runner.produce_step

            def wrapped(*args, **kwargs):
                started.set()
                time.sleep(0.2)
                return original(*args, **kwargs)

            desktop_runner.produce_step = wrapped  # type: ignore[assignment]
            try:
                with desktop_runner.BatchBind(root):
                    thread = threading.Thread(
                        target=lambda: produce_batch(
                            root,
                            limit=1,
                            workers=1,
                            provider="fixture",
                            fixture_root=fixture,
                        ),
                        daemon=True,
                    )
                    thread.start()
                    self.assertTrue(started.wait(60))
                    request_soft_stop(root)
                    thread.join(180)
                self.assertFalse(thread.is_alive())
                queue = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))
                status = queue["tasks"][0]["status"]
                self.assertIn(status, {"queued", "passed", "blocked", "gate_failed"})
                if status == "queued":
                    sample_dir = root / queue["tasks"][0]["sample_dir"]
                    self.assertTrue(sample_dir.exists())
                    with desktop_runner.BatchBind(root):
                        summary = produce_batch(
                            root,
                            limit=1,
                            workers=1,
                            provider="fixture",
                            fixture_root=fixture,
                        )
                    self.assertGreaterEqual(summary["claimed"], 1)
                    queue = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))
                    self.assertIn(queue["tasks"][0]["status"], {"passed", "gate_failed", "blocked"})
            finally:
                desktop_runner.produce_step = original  # type: ignore[assignment]

    def test_claim_limit_drains_leftover_queued(self) -> None:
        self.assertEqual(desktop_runner.claim_limit_for_run(9, 0), 9)
        self.assertEqual(desktop_runner.claim_limit_for_run(9, 10), 10)
        self.assertEqual(desktop_runner.claim_limit_for_run(9, 1), 9)

    def test_run_pipeline_does_not_leave_queued_when_limit_below_queue(self) -> None:
        """Previous leftover 排队 plus this batch must all be claimed, not left behind."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            root.mkdir()
            ensure_workspace(root)
            fixture = root / "fixtures"
            _make_fixture(fixture, correct_count=2)
            domain_cat = root / "materials" / "example" / "CATALOG.json"
            packs = json.loads(domain_cat.read_text(encoding="utf-8"))
            packs["packs"].append(
                {
                    "pack": "sample-pack-b",
                    "path": "materials/example/sample-pack-b",
                    "coldness": "cold",
                    "status": "READY",
                }
            )
            domain_cat.write_text(json.dumps(packs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            pack_b = root / "materials" / "example" / "sample-pack-b"
            pack_b.mkdir(parents=True)
            (pack_b / "CATALOG.json").write_text(
                json.dumps({"pack": "sample-pack-b", "status": "READY", "docs": []}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            for slug, pack in (("demo-a", "sample-pack"), ("demo-b", "sample-pack-b")):
                staging = root / "data" / "staging" / slug
                staging.mkdir(parents=True)
                (staging / "context.txt").write_text(_context(), encoding="utf-8")
                (staging / "context.md").write_text(_context(), encoding="utf-8")
                _write_json(staging / "sources.json", {"domain": "test", "docs": [{"doc_id": "D01"}, {"doc_id": "D02"}]})
                _write_json(
                    staging / "meta.json",
                    {"slug": slug, "domain": "example", "materials_pack": f"materials/example/{pack}"},
                )
                qw_json(
                    root,
                    [
                        "enqueue",
                        "--slug",
                        slug,
                        "--domain",
                        "example",
                        "--staging",
                        f"data/staging/{slug}",
                        "--materials-pack",
                        f"materials/example/{pack}",
                        "--provider-mode",
                        "fixture",
                        "--include-used",
                    ],
                )
            queue = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))
            self.assertEqual([t["status"] for t in queue["tasks"]], ["queued", "queued"])
            result = desktop_runner.run_pipeline(
                root,
                limit=1,
                workers=2,
                provider="fixture",
                fixture_root=fixture,
                skip_stage=True,
                include_used=True,
            )
            self.assertTrue(result.get("ok"), result)
            queue = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))
            leftover = [t["id"] for t in queue["tasks"] if t.get("status") == "queued"]
            self.assertEqual(leftover, [], msg="queued leftover after batch: " + json.dumps(leftover))
            self.assertGreaterEqual(result["summary"]["claimed"], 2)


class DesktopArchiveTests(unittest.TestCase):
    def test_fixture_pass_writes_slim_runs_without_sources(self) -> None:
        from workflow import batch_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            root.mkdir()
            ensure_workspace(root)
            fixture = root / "fixtures"
            _make_fixture(fixture, correct_count=2)
            task_root = root / "work" / "samples" / "201-desktop-slim"
            (task_root / "source" / "raw").mkdir(parents=True)
            (task_root / "source" / "context.txt").write_text(_context(), encoding="utf-8")
            (task_root / "source" / "context.md").write_text(_context(), encoding="utf-8")
            (task_root / "source" / "raw" / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
            _write_json(task_root / "source" / "sources.json", {"domain": "test", "docs": [{"doc_id": "D01"}, {"doc_id": "D02"}]})
            task = {
                "id": "t-desktop-slim",
                "slug": "desktop-slim",
                "sample_dir": "work/samples/201-desktop-slim",
                "sample_id": "desktop-201",
                "context_unit_id": "ctx-201",
                "provider_mode": "fixture",
            }
            with desktop_runner.BatchBind(root):
                result = batch_run.run_candidate(
                    task,
                    index=1,
                    system=root / "scripts" / "prompts" / "generate_qa.txt",
                    gen_retries=1,
                    provider="fixture",
                    fixture_root=fixture,
                    source_type="test",
                )
            self.assertEqual(result["status"], "passed")
            run_root = root / "archive" / "runs" / "desktop-slim"
            run_dirs = [p for p in run_root.iterdir() if p.is_dir()] if run_root.is_dir() else []
            self.assertEqual(len(run_dirs), 1)
            dest = run_dirs[0]
            self.assertTrue(dest.name.endswith("-passed"))
            self.assertFalse((dest / "sources").exists())
            self.assertFalse(list(dest.rglob("*.pdf")))
            self.assertFalse(any("technical-failed" in p.name for p in run_dirs))
            self.assertTrue((dest / "raw" / "qa_parsed.json").is_file())
            self.assertTrue((dest / "raw" / "rollout_8.json").is_file())
            self.assertFalse((dest / "raw" / "generate_input.txt").exists())

    def test_technical_failed_label_does_not_create_run_bucket(self) -> None:
        from workflow import batch_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            root.mkdir()
            ensure_workspace(root)
            candidate = root / "work" / "samples" / "202-tech" / "work" / "candidates" / "candidate-01"
            (candidate / "work" / "raw").mkdir(parents=True)
            (candidate / "work" / "raw" / "qa_parsed.json").write_text("{}", encoding="utf-8")
            task = {"id": "t-desktop-tech", "slug": "desktop-tech", "context_unit_id": "ctx-202", "provider_mode": "fixture"}
            with desktop_runner.BatchBind(root):
                dest = batch_run.archive_run(candidate, task, "candidate-01", 1, "technical-failed")
            self.assertEqual(dest, candidate / "work")
            self.assertFalse(list((root / "archive" / "runs").glob("**/technical-failed")))
            slug_dir = root / "archive" / "runs" / "desktop-tech"
            self.assertFalse(slug_dir.exists())


class DesktopSettingsTests(unittest.TestCase):
    def test_save_and_mask_three_roles(self) -> None:
        import tempfile
        from unittest.mock import patch

        from desktop.backend import settings as settings_mod

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings_path = config / "settings.json"
            keys_path = config / "keys.env"
            with patch.object(settings_mod, "CONFIG_DIR", config), patch.object(
                settings_mod, "SETTINGS_PATH", settings_path
            ), patch.object(settings_mod, "KEYS_PATH", keys_path), patch.object(
                settings_mod, "LEGACY_KEY_FILE", config / "missing-ai-keys.env"
            ), patch.object(settings_mod, "LEGACY_VANKIT", config / "missing-vankit.txt"):
                public = settings_mod.save_settings(
                    {
                        "roles": {
                            "generation": {
                                "model": "gpt-custom-gen",
                                "base_url": "https://relay.example/v1",
                                "api_key": "gen-secret-key-123456",
                            },
                            "evaluation": {
                                "model": "qwen-custom",
                                "base_url": "https://dash.example/v1",
                                "api_key": "eval-secret-key-123456",
                            },
                            "judge": {
                                "model": "luna-custom",
                                "base_url": "https://openai.example/v1",
                                "api_key": "judge-secret-key-123456",
                            },
                        }
                    }
                )
                self.assertTrue(public["ready"])
                self.assertEqual(public["roles"]["generation"]["model"], "gpt-custom-gen")
                self.assertEqual(public["roles"]["evaluation"]["base_url"], "https://dash.example/v1")
                self.assertEqual(public["roles"]["judge"]["base_url"], "https://openai.example/v1")
                self.assertTrue(public["roles"]["generation"]["api_key_set"])
                self.assertNotIn("gen-secret-key-123456", public["roles"]["generation"]["api_key_mask"])
                again = settings_mod.save_settings(
                    {
                        "roles": {
                            "generation": {"model": "gpt-custom-gen", "base_url": "https://relay.example/v1", "api_key": ""},
                            "evaluation": {"model": "qwen-custom", "base_url": "https://dash.example/v1", "api_key": ""},
                            "judge": {"model": "luna-custom", "base_url": "https://openai.example/v1", "api_key": ""},
                        }
                    }
                )
                self.assertTrue(again["roles"]["judge"]["api_key_set"])
                env = settings_mod.apply_to_environ({})
                self.assertEqual(env["LCQA_GEN_MODEL"], "gpt-custom-gen")
                self.assertEqual(env["LCQA_EVAL_BASE_URL"], "https://dash.example/v1")
                self.assertEqual(env["OPENAI_BASE_URL"], "https://openai.example/v1")
                self.assertEqual(env["DASHSCOPE_API_KEY"], "eval-secret-key-123456")
                self.assertEqual(env["OPENAI_API_KEY"], "judge-secret-key-123456")
                self.assertEqual(env["LCQA_GEN_REASONING_EFFORT"], "medium")
                self.assertEqual(env["LCQA_EVAL_REASONING_EFFORT"], "high")
                self.assertEqual(env["LCQA_JUDGE_REASONING_EFFORT"], "medium")
                text = keys_path.read_text(encoding="utf-8")
                self.assertIn("LCQA_GEN_API_KEY=", text)
                self.assertIn("DASHSCOPE_API_KEY=", text)
                self.assertIn("OPENAI_API_KEY=", text)

    def test_save_reasoning_effort_and_reject_invalid(self) -> None:
        import tempfile
        from unittest.mock import patch

        from desktop.backend import settings as settings_mod

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings_path = config / "settings.json"
            keys_path = config / "keys.env"
            with patch.object(settings_mod, "CONFIG_DIR", config), patch.object(
                settings_mod, "SETTINGS_PATH", settings_path
            ), patch.object(settings_mod, "KEYS_PATH", keys_path), patch.object(
                settings_mod, "LEGACY_KEY_FILE", config / "missing-ai-keys.env"
            ), patch.object(settings_mod, "LEGACY_VANKIT", config / "missing-vankit.txt"):
                public = settings_mod.save_settings(
                    {
                        "roles": {
                            "generation": {
                                "model": "gpt-custom-gen",
                                "base_url": "https://relay.example/v1",
                                "reasoning_effort": "xhigh",
                                "api_key": "gen-secret-key-123456",
                            },
                            "evaluation": {
                                "model": "qwen-custom",
                                "base_url": "https://dash.example/v1",
                                "reasoning_effort": "low",
                                "api_key": "eval-secret-key-123456",
                            },
                            "judge": {
                                "model": "luna-custom",
                                "base_url": "https://openai.example/v1",
                                "reasoning_effort": "none",
                                "api_key": "judge-secret-key-123456",
                            },
                        }
                    }
                )
                self.assertEqual(public["roles"]["generation"]["reasoning_effort"], "xhigh")
                self.assertEqual(public["roles"]["evaluation"]["reasoning_effort"], "low")
                self.assertEqual(public["roles"]["judge"]["reasoning_effort"], "none")
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["generation"]["reasoning_effort"], "xhigh")
                env = settings_mod.apply_to_environ({})
                self.assertEqual(env["LCQA_GEN_REASONING_EFFORT"], "xhigh")
                self.assertEqual(env["LCQA_EVAL_REASONING_EFFORT"], "low")
                self.assertEqual(env["LCQA_JUDGE_REASONING_EFFORT"], "none")
                with self.assertRaises(ValueError):
                    settings_mod.save_settings(
                        {
                            "roles": {
                                "generation": {"reasoning_effort": "ultra"},
                            }
                        }
                    )

    def test_save_omitting_effort_keeps_existing_and_writes_key(self) -> None:
        import tempfile
        from unittest.mock import patch

        from desktop.backend import settings as settings_mod

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings_path = config / "settings.json"
            keys_path = config / "keys.env"
            settings_path.write_text(
                json.dumps(
                    {
                        "generation": {"model": "gpt-custom-gen", "base_url": "", "reasoning_effort": "xhigh"},
                        "evaluation": {"model": "qwen-custom", "base_url": "", "reasoning_effort": "low"},
                        "judge": {"model": "luna-custom", "base_url": "", "reasoning_effort": "none"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(settings_mod, "CONFIG_DIR", config), patch.object(
                settings_mod, "SETTINGS_PATH", settings_path
            ), patch.object(settings_mod, "KEYS_PATH", keys_path), patch.object(
                settings_mod, "LEGACY_KEY_FILE", config / "missing-ai-keys.env"
            ), patch.object(settings_mod, "LEGACY_VANKIT", config / "missing-vankit.txt"):
                public = settings_mod.save_settings(
                    {
                        "roles": {
                            "generation": {"model": "gpt-custom-gen", "base_url": "", "api_key": ""},
                            "evaluation": {"model": "qwen-custom", "base_url": "", "api_key": ""},
                            "judge": {"model": "luna-custom", "base_url": "", "api_key": ""},
                        }
                    }
                )
                self.assertEqual(public["roles"]["generation"]["reasoning_effort"], "xhigh")
                self.assertEqual(public["roles"]["evaluation"]["reasoning_effort"], "low")
                self.assertEqual(public["roles"]["judge"]["reasoning_effort"], "none")
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["generation"]["reasoning_effort"], "xhigh")
                self.assertEqual(saved["evaluation"]["reasoning_effort"], "low")
                self.assertEqual(saved["judge"]["reasoning_effort"], "none")

    def test_missing_reasoning_effort_defaults_per_role(self) -> None:
        import tempfile
        from unittest.mock import patch

        from desktop.backend import settings as settings_mod

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings_path = config / "settings.json"
            keys_path = config / "keys.env"
            settings_path.write_text(
                json.dumps(
                    {
                        "generation": {"model": "gpt-5.6-sol", "base_url": ""},
                        "evaluation": {"model": "qwen3.5-35b-a3b", "base_url": ""},
                        "judge": {"model": "gpt-5.6-luna", "base_url": "https://api.openai.com/v1"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(settings_mod, "CONFIG_DIR", config), patch.object(
                settings_mod, "SETTINGS_PATH", settings_path
            ), patch.object(settings_mod, "KEYS_PATH", keys_path), patch.object(
                settings_mod, "LEGACY_KEY_FILE", config / "missing-ai-keys.env"
            ), patch.object(settings_mod, "LEGACY_VANKIT", config / "missing-vankit.txt"):
                public = settings_mod.get_settings_public()
                self.assertEqual(public["roles"]["generation"]["reasoning_effort"], "medium")
                self.assertEqual(public["roles"]["evaluation"]["reasoning_effort"], "high")
                self.assertEqual(public["roles"]["judge"]["reasoning_effort"], "medium")
                self.assertEqual(public["roles"]["review"]["reasoning_effort"], "medium")
                self.assertEqual(public["roles"]["material_audit"]["reasoning_effort"], "medium")

    def test_review_and_audit_fall_back_to_judge_and_pipeline_ready_ignores_aux(self) -> None:
        import tempfile
        from unittest.mock import patch

        from desktop.backend import settings as settings_mod

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            settings_path = config / "settings.json"
            keys_path = config / "keys.env"
            with patch.object(settings_mod, "CONFIG_DIR", config), patch.object(
                settings_mod, "SETTINGS_PATH", settings_path
            ), patch.object(settings_mod, "KEYS_PATH", keys_path), patch.object(
                settings_mod, "LEGACY_KEY_FILE", config / "missing-ai-keys.env"
            ), patch.object(settings_mod, "LEGACY_VANKIT", config / "missing-vankit.txt"):
                public = settings_mod.save_settings(
                    {
                        "roles": {
                            "generation": {
                                "model": "gpt-custom-gen",
                                "base_url": "https://relay.example/v1",
                                "api_key": "gen-secret-key-123456",
                            },
                            "evaluation": {
                                "model": "qwen-custom",
                                "base_url": "https://dash.example/v1",
                                "api_key": "eval-secret-key-123456",
                            },
                            "judge": {
                                "model": "luna-custom",
                                "base_url": "https://openai.example/v1",
                                "api_key": "judge-secret-key-123456",
                            },
                        }
                    }
                )
                self.assertTrue(public["ready"])
                self.assertTrue(public["review_ready"])
                self.assertTrue(public["material_audit_ready"])
                self.assertEqual(public["roles"]["review"]["api_key_source"], "judge")
                self.assertEqual(public["roles"]["material_audit"]["api_key_source"], "judge")
                self.assertEqual(public["roles"]["review"]["model"], "gpt-5.6-luna")
                env = settings_mod.apply_to_environ({})
                self.assertEqual(env["LCQA_REVIEW_API_KEY"], "judge-secret-key-123456")
                self.assertEqual(env["LCQA_AUDIT_API_KEY"], "judge-secret-key-123456")
                self.assertEqual(env["LCQA_REVIEW_BASE_URL"], "https://openai.example/v1")
                own = settings_mod.save_settings(
                    {
                        "roles": {
                            "review": {
                                "model": "review-custom",
                                "base_url": "https://review.example/v1",
                                "api_key": "review-secret-key-123456",
                            }
                        }
                    }
                )
                self.assertEqual(own["roles"]["review"]["api_key_source"], "own")
                self.assertEqual(own["roles"]["review"]["model"], "review-custom")
                env2 = settings_mod.apply_to_environ({})
                self.assertEqual(env2["LCQA_REVIEW_API_KEY"], "review-secret-key-123456")
                self.assertEqual(env2["LCQA_AUDIT_API_KEY"], "judge-secret-key-123456")


class LcqaReasoningTests(unittest.TestCase):
    def test_qwen_and_openai_effort_mapping(self) -> None:
        sys.path.insert(0, str(CODE_ROOT / "scripts"))
        from lcqa_reasoning import openai_responses_kwargs, qwen_extra_body

        self.assertEqual(openai_responses_kwargs("high"), {"reasoning": {"effort": "high"}})
        self.assertEqual(qwen_extra_body("none"), {"enable_thinking": False})
        self.assertEqual(
            qwen_extra_body("high"),
            {"enable_thinking": True, "thinking_budget": 16384},
        )
        self.assertEqual(
            qwen_extra_body("max", "qwen3.8-max"),
            {"enable_thinking": True, "reasoning_effort": "xhigh"},
        )


class MaterialSelectTests(unittest.TestCase):
    def test_scan_materials_exposes_slug_and_domain_key(self):
        from desktop.backend.materials import scan_materials

        root = CODE_ROOT
        data = scan_materials(root)
        self.assertGreater(data.get("pack_count", 0), 0)
        found = False
        kinds = set()
        named = {}
        for domain in data["domains"]:
            self.assertTrue(domain.get("domain_key"))
            for pack in domain.get("packs") or []:
                self.assertEqual(pack.get("domain_key"), domain["domain_key"])
                self.assertTrue(pack.get("slug"))
                self.assertIn(pack.get("doc_kind"), {"single", "multi", "unknown"})
                self.assertIsInstance(pack.get("doc_count"), int)
                if pack["doc_kind"] == "single":
                    self.assertEqual(pack["doc_count"], 1)
                elif pack["doc_kind"] == "multi":
                    self.assertGreaterEqual(pack["doc_count"], 2)
                else:
                    self.assertEqual(pack["doc_count"], 0)
                kinds.add(pack["doc_kind"])
                named[str(pack.get("path") or "")] = pack
                found = True
        self.assertTrue(found)
        self.assertIn("single", kinds)
        self.assertIn("multi", kinds)
        thinking = named.get("materials/detective/thinking-machine")
        raffles = named.get("materials/detective/raffles")
        if thinking:
            self.assertEqual(thinking["doc_kind"], "single")
            self.assertEqual(thinking["doc_count"], 1)
        if raffles:
            self.assertEqual(raffles["doc_kind"], "multi")
            self.assertGreaterEqual(raffles["doc_count"], 2)

    def test_run_pipeline_rejects_empty_pack_list(self):
        with self.assertRaises(ValueError):
            desktop_runner.run_pipeline(
                CODE_ROOT,
                limit=1,
                workers=1,
                provider="fixture",
                skip_stage=True,
                packs=[{"domain_key": "", "pack": ""}],
            )

    def test_stage_selected_packs_do_not_force_include_used(self):
        """自选路径默认不升格 include_used；仅请求体显式为真时才带 --include-used。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ws"
            (root / "queue").mkdir(parents=True)
            (root / "workflow").mkdir(parents=True)
            stage_bin = root / "workflow" / "stage_from_materials.py"
            stage_bin.write_text("#!/usr/bin/env python3\nprint('{}')\n", encoding="utf-8")
            (root / "workflow" / "queue_worker.py").write_text(
                "#!/usr/bin/env python3\nprint('{}')\n", encoding="utf-8"
            )
            captured: list[list[str]] = []

            def fake_popen(cmd, workspace, **kwargs):
                captured.append(list(cmd))
                manifest = root / "queue" / "pending_packs.json"
                if "stage_from_materials.py" in " ".join(cmd):
                    _write_json(
                        manifest,
                        {
                            "packs": [
                                {
                                    "slug": "example-demo",
                                    "domain": "example",
                                    "staging": "data/staging/example-demo",
                                    "materials_pack": "materials/example/demo",
                                }
                            ]
                        },
                    )
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

            with (
                patch.object(desktop_runner, "popen_run", side_effect=fake_popen),
                patch.object(desktop_runner, "emit_event"),
                patch.object(desktop_runner, "raise_if_paused"),
            ):
                desktop_runner._stage_and_enqueue(
                    root,
                    limit=1,
                    prefer_cold=True,
                    include_hot=False,
                    domain=None,
                    provider="fixture",
                    include_used=False,
                    packs=[{"domain_key": "example", "pack": "demo"}],
                )
                stage_cmds = [c for c in captured if "stage_from_materials.py" in " ".join(c)]
                enq_cmds = [c for c in captured if "enqueue-bulk" in c]
                self.assertTrue(stage_cmds)
                self.assertNotIn("--include-used", stage_cmds[0])
                self.assertTrue(enq_cmds)
                self.assertNotIn("--include-used", enq_cmds[0])
                self.assertIn("--question-type", enq_cmds[0])
                self.assertEqual(enq_cmds[0][enq_cmds[0].index("--question-type") + 1], "short_answer")

                captured.clear()
                desktop_runner._stage_and_enqueue(
                    root,
                    limit=1,
                    prefer_cold=True,
                    include_hot=False,
                    domain=None,
                    provider="fixture",
                    include_used=True,
                    packs=[{"domain_key": "example", "pack": "demo"}],
                    question_type="auto",
                )
                stage_cmds = [c for c in captured if "stage_from_materials.py" in " ".join(c)]
                enq_cmds = [c for c in captured if "enqueue-bulk" in c]
                self.assertIn("--include-used", stage_cmds[0])
                self.assertIn("--include-used", enq_cmds[0])
                self.assertEqual(enq_cmds[0][enq_cmds[0].index("--question-type") + 1], "auto")

    def test_run_body_accepts_board_question_type(self) -> None:
        from pydantic import ValidationError

        from desktop.backend.server import RunBody

        body = RunBody(limit=1, workers=1, question_type="multiple_choice")
        self.assertEqual(body.question_type, "multiple_choice")
        auto = RunBody(question_type="auto")
        self.assertEqual(auto.question_type, "auto")
        default = RunBody()
        self.assertEqual(default.question_type, "short_answer")
        with self.assertRaises(ValidationError):
            RunBody(question_type="essay")
        pinned = RunBody(task_ids=["t-1", "t-2"], limit=2)
        self.assertEqual(pinned.task_ids, ["t-1", "t-2"])

    def test_claim_one_passes_task_id(self) -> None:
        from pathlib import Path

        with patch.object(desktop_runner, "qw_json", return_value={"id": "t-queued", "status": "claimed"}) as mocked:
            task = desktop_runner.claim_one(Path("/tmp"), "w-1", task_id="t-queued")
        self.assertEqual(task["id"], "t-queued")
        args = mocked.call_args[0][1]
        self.assertEqual(args[0], "claim")
        self.assertEqual(args[args.index("--task-id") + 1], "t-queued")

    def test_annotate_queue_marks_queued_can_start(self) -> None:
        from desktop.backend.server import _annotate_queue

        queue = _annotate_queue(
            {
                "tasks": [
                    {"id": "t-q", "slug": "demo", "status": "queued"},
                    {"id": "t-p", "slug": "done", "status": "passed"},
                ]
            }
        )
        by_id = {t["id"]: t for t in queue["tasks"]}
        self.assertTrue(by_id["t-q"]["can_start"])
        self.assertFalse(by_id["t-p"]["can_start"])


class UiPrefsAndQueueOpsTests(unittest.TestCase):
    def test_ui_prefs_roundtrip(self):
        from desktop.backend import ui_prefs

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lcqa-desktop.json"
            with patch.object(ui_prefs, "APP_CONFIG", path):
                prefs = ui_prefs.save_ui_prefs(
                    {
                        "auto_open_workspace": True,
                        "last_workspace": "/tmp/demo",
                        "board": {
                            "selected_statuses": ["queued", "running"],
                            "date_from": "2026-09-07",
                            "date_to": "2026-09-08",
                            "question_type": "auto",
                        },
                        "materials": {"selected_domains": ["academic"], "selected_statuses": ["READY"]},
                    }
                )
                self.assertEqual(prefs["last_workspace"], "/tmp/demo")
                self.assertEqual(prefs["board"]["selected_statuses"], ["queued", "running"])
                self.assertEqual(prefs["board"]["date_from"], "2026-09-07")
                self.assertEqual(prefs["board"]["date_to"], "2026-09-08")
                self.assertEqual(prefs["board"]["question_type"], "auto")
                again = ui_prefs.get_ui_prefs()
                self.assertEqual(again["materials"]["selected_domains"], ["academic"])
                self.assertEqual(again["board"]["date_from"], "2026-09-07")
                ui_prefs.push_recent(Path(directory) / "ws-a")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(any(str(p).endswith("ws-a") for p in data["recents"]))
                self.assertTrue(str(data["ui"]["last_workspace"]).endswith("ws-a"))

    def test_non_accuracy_failure_detection(self):
        from desktop.backend.queue_ops import is_non_accuracy_failure

        self.assertTrue(
            is_non_accuracy_failure(
                {
                    "status": "gate_failed",
                    "avg_accuracy": None,
                    "candidate_results": [{"status": "precheck_failed", "avg_accuracy": None}],
                }
            )
        )
        self.assertTrue(
            is_non_accuracy_failure(
                {
                    "status": "blocked",
                    "avg_accuracy": None,
                    "review_status": "technical_failed",
                }
            )
        )
        self.assertFalse(is_non_accuracy_failure({"status": "gate_failed", "avg_accuracy": 1.0}))
        self.assertFalse(is_non_accuracy_failure({"status": "passed", "avg_accuracy": 0.375}))

    def test_release_active_tasks_sweeps_running(self):
        from desktop.backend import queue_ops

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "queue" / "queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "tasks": [
                            {
                                "id": "t-x",
                                "slug": "demo",
                                "status": "running",
                                "worker_id": "dead",
                                "claimed_at": "2026-01-01T00:00:00+00:00",
                                "history": [],
                            },
                            {"id": "t-y", "slug": "ok", "status": "queued", "history": []},
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_qw(_workspace, args):
                task_id = args[args.index("--task-id") + 1]
                data = json.loads(queue_path.read_text(encoding="utf-8"))
                for task in data["tasks"]:
                    if task["id"] == task_id:
                        task["status"] = "queued"
                        task["worker_id"] = None
                        task["claimed_at"] = None
                        task["history"].append({"event": "released"})
                queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return next(t for t in data["tasks"] if t["id"] == task_id)

            with patch.object(queue_ops, "_qw", side_effect=fake_qw):
                result = queue_ops.release_active_tasks(root, reason="test")
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["released"], ["t-x"])
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            statuses = {t["id"]: t["status"] for t in data["tasks"]}
            self.assertEqual(statuses["t-x"], "queued")
            self.assertIsNone(next(t for t in data["tasks"] if t["id"] == "t-x").get("worker_id"))
            self.assertEqual(statuses["t-y"], "queued")

    def test_reset_sample_work_for_retry_clears_terminal_state(self):
        from desktop.backend.queue_ops import _reset_sample_work_for_retry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / "work" / "samples" / "024-demo"
            work = sample / "work" / "candidates" / "candidate-01" / "work"
            raw = work / "raw"
            raw.mkdir(parents=True)
            (raw / "precheck.json").write_text('{"ok": false}', encoding="utf-8")
            (work / "candidate_state.json").write_text(
                json.dumps({"status": "precheck_failed", "attempt": 2}),
                encoding="utf-8",
            )
            (sample / "work" / "candidate_results.json").write_text("{}", encoding="utf-8")
            (sample / "source").mkdir(parents=True)
            (sample / "source" / "context.md").write_text("keep", encoding="utf-8")

            result = _reset_sample_work_for_retry(root, "work/samples/024-demo")
            self.assertTrue(result["ok"])
            self.assertFalse((work / "candidate_state.json").exists())
            self.assertFalse(raw.exists())
            self.assertFalse((sample / "work" / "candidate_results.json").exists())
            self.assertTrue((sample / "source" / "context.md").is_file())


class BoardOpsTests(unittest.TestCase):
    def test_task_ended_at_from_complete_history(self):
        from desktop.backend.queue_ops import task_ended_at

        task = {
            "status": "gate_failed",
            "history": [
                {"at": "2026-09-07T11:20:07+00:00", "event": "claimed"},
                {
                    "at": "2026-09-07T11:22:29+00:00",
                    "event": "complete",
                    "detail": "result=gate_failed",
                },
            ],
        }
        self.assertEqual(task_ended_at(task), "2026-09-07T11:22:29+00:00")
        self.assertIsNone(task_ended_at({"status": "queued", "history": task["history"]}))

    def test_can_requeue_cancelled_and_non_accuracy(self):
        from desktop.backend.queue_ops import can_cancel, can_requeue

        self.assertTrue(can_requeue({"status": "cancelled"}))
        self.assertFalse(can_cancel({"status": "cancelled"}))
        self.assertTrue(
            can_requeue(
                {
                    "status": "gate_failed",
                    "avg_accuracy": None,
                    "candidate_results": [{"status": "precheck_failed"}],
                }
            )
        )
        self.assertTrue(
            can_cancel(
                {
                    "status": "gate_failed",
                    "avg_accuracy": None,
                    "candidate_results": [{"status": "precheck_failed"}],
                }
            )
        )
        self.assertFalse(can_requeue({"status": "gate_failed", "avg_accuracy": 0.75}))
        self.assertFalse(can_cancel({"status": "gate_failed", "avg_accuracy": 1.0}))
        self.assertFalse(can_requeue({"status": "passed", "avg_accuracy": 0.375}))

    def test_can_start_queued_only(self):
        from desktop.backend.queue_ops import can_start

        self.assertTrue(can_start({"status": "queued"}))
        self.assertFalse(can_start({"status": "claimed"}))
        self.assertFalse(can_start({"status": "passed"}))
        self.assertFalse(can_start({"status": "cancelled"}))

    def test_can_requeue_blocked_by_pack_sibling(self):
        from desktop.backend.queue_ops import can_requeue, find_pack_sibling

        queue = {
            "tasks": [
                {
                    "id": "t-pass",
                    "slug": "example-demo",
                    "materials_pack": "materials/example/demo",
                    "status": "passed",
                },
                {
                    "id": "t-fail",
                    "slug": "example-demo",
                    "materials_pack": "materials/example/demo",
                    "status": "gate_failed",
                    "avg_accuracy": None,
                    "candidate_results": [{"status": "precheck_failed"}],
                },
            ]
        }
        fail = queue["tasks"][1]
        self.assertTrue(can_requeue(fail))  # without queue context still true
        self.assertFalse(can_requeue(fail, queue=queue))
        sib = find_pack_sibling(queue, fail)
        self.assertIsNotNone(sib)
        assert sib is not None
        self.assertEqual(sib["id"], "t-pass")
        cancelled = {**fail, "id": "t-old", "status": "cancelled"}
        queue["tasks"].append(cancelled)
        self.assertFalse(can_requeue(cancelled, queue=queue))

    def test_only_preferred_cancelled_may_requeue(self):
        from desktop.backend.queue_ops import can_requeue, find_pack_sibling

        queue = {
            "tasks": [
                {
                    "id": "t-0008",
                    "slug": "literature-detective-cold-classics",
                    "materials_pack": "materials/literature/detective-cold-classics",
                    "status": "cancelled",
                },
                {
                    "id": "t-0012",
                    "slug": "literature-detective-cold-classics",
                    "materials_pack": "materials/literature/detective-cold-classics",
                    "status": "cancelled",
                },
            ]
        }
        old, new = queue["tasks"]
        self.assertFalse(can_requeue(old, queue=queue))
        self.assertTrue(can_requeue(new, queue=queue))
        sib = find_pack_sibling(queue, old)
        self.assertEqual(sib["id"], "t-0012")
        self.assertIsNone(find_pack_sibling(queue, new))

    def test_can_review_pass_manual_review_only(self):
        from desktop.backend.queue_ops import can_review_pass

        self.assertTrue(
            can_review_pass(
                {
                    "status": "blocked",
                    "review_status": "manual_review",
                    "candidate_results": [
                        {
                            "status": "manual_review",
                            "avg_accuracy": 0.0,
                            "candidate_dir": "work/samples/x/work/candidates/candidate-01",
                            "candidate_id": "candidate-01",
                            "candidate_index": 1,
                            "attempt": 2,
                        }
                    ],
                }
            )
        )
        self.assertFalse(
            can_review_pass(
                {
                    "status": "blocked",
                    "review_status": "technical_failed",
                    "candidate_results": [{"status": "technical_failed"}],
                }
            )
        )
        self.assertFalse(
            can_review_pass(
                {
                    "status": "gate_failed",
                    "review_status": "manual_review",
                    "candidate_results": [{"status": "manual_review", "avg_accuracy": 0.0}],
                }
            )
        )

    def test_can_human_reject_and_revoke_delivery(self):
        from desktop.backend.queue_ops import can_human_reject, can_requeue, human_reject_passed

        self.assertTrue(can_human_reject({"status": "passed"}))
        self.assertFalse(can_human_reject({"status": "gate_failed"}))
        self.assertTrue(
            can_human_reject(
                {
                    "status": "blocked",
                    "review_status": "manual_review",
                    "candidate_results": [{"status": "manual_review", "avg_accuracy": 0.0}],
                }
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            delivery = root / "samples" / "022-demo-c01"
            gold = delivery / "questions" / "q01" / "data" / "gold.json"
            gold.parent.mkdir(parents=True)
            gold.write_text("{}", encoding="utf-8")
            (delivery / "sample_qc.md").write_text("# qc\n", encoding="utf-8")
            pack = root / "materials" / "example" / "demo-pack"
            pack.mkdir(parents=True)
            (pack / "CATALOG.json").write_text(
                json.dumps({"pack": "demo-pack", "status": "USED_IN_SAMPLE_022"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (pack.parent / "CATALOG.json").write_text(
                json.dumps(
                    {
                        "domain": "example",
                        "packs": [{"pack": "demo-pack", "status": "USED_IN_SAMPLE_022"}],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path = root / "queue" / "queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "tasks": [
                            {
                                "id": "t-pass",
                                "slug": "demo",
                                "status": "passed",
                                "review_status": "passed",
                                "avg_accuracy": 0.375,
                                "sample_dir": "work/samples/022-demo",
                                "materials_pack": "materials/example/demo-pack",
                                "history": [],
                                "candidate_results": [
                                    {
                                        "status": "passed",
                                        "sample_dir": "samples/022-demo-c01",
                                        "candidate_dir": "work/samples/022-demo/work/candidates/candidate-01",
                                        "avg_accuracy": 0.375,
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            result = human_reject_passed(root, task_id="t-pass", reason="题干剧透", requeue=False)
            self.assertTrue(result["ok"])
            self.assertFalse(delivery.exists())
            self.assertTrue((root / "archive" / "failed-samples" / "022-demo-c01" / "HUMAN_REJECT.md").is_file())
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            task = data["tasks"][0]
            self.assertEqual(task["status"], "gate_failed")
            self.assertEqual(task["review_status"], "human_reject")
            self.assertIn("剧透", task["failure_reason"])
            self.assertTrue(can_requeue(task))
            pack_cat = json.loads((pack / "CATALOG.json").read_text(encoding="utf-8"))
            self.assertEqual(pack_cat["status"], "READY")

            # second: requeue path
            delivery2 = root / "samples" / "023-demo-c01"
            gold2 = delivery2 / "questions" / "q01" / "data" / "gold.json"
            gold2.parent.mkdir(parents=True)
            gold2.write_text("{}", encoding="utf-8")
            (delivery2 / "sample_qc.md").write_text("# qc\n", encoding="utf-8")
            work = root / "work" / "samples" / "023-demo" / "work" / "candidates" / "candidate-01" / "work"
            raw = work / "raw"
            raw.mkdir(parents=True)
            (raw / "judge_summary.json").write_text("{}", encoding="utf-8")
            (work / "candidate_state.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
            pack2 = root / "materials" / "example" / "demo-pack-2"
            pack2.mkdir(parents=True)
            (pack2 / "CATALOG.json").write_text(
                json.dumps({"pack": "demo-pack-2", "status": "USED_IN_SAMPLE_023"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            data["tasks"].append(
                {
                    "id": "t-pass2",
                    "slug": "demo2",
                    "status": "passed",
                    "review_status": "passed",
                    "avg_accuracy": 0.25,
                    "sample_dir": "work/samples/023-demo",
                    "materials_pack": "materials/example/demo-pack-2",
                    "history": [],
                    "candidate_results": [{"status": "passed", "sample_dir": "samples/023-demo-c01"}],
                }
            )
            queue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (pack2 / "CATALOG.json").write_text(
                json.dumps({"pack": "demo-pack-2", "status": "USED_IN_SAMPLE_023"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            def fake_qw(_workspace, args):
                task_id = args[args.index("--task-id") + 1]
                status = args[args.index("--status") + 1]
                q = json.loads(queue_path.read_text(encoding="utf-8"))
                for t in q["tasks"]:
                    if t["id"] == task_id:
                        t["status"] = status
                        t.setdefault("history", []).append({"event": "status", "detail": f"→ {status}"})
                        queue_path.write_text(json.dumps(q, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        return t
                raise RuntimeError("missing")

            from desktop.backend import queue_ops

            with patch.object(queue_ops, "_qw", side_effect=fake_qw):
                result2 = human_reject_passed(root, task_id="t-pass2", reason="金标错", requeue=True)
            self.assertTrue(result2["ok"])
            q2 = json.loads(queue_path.read_text(encoding="utf-8"))
            t2 = next(t for t in q2["tasks"] if t["id"] == "t-pass2")
            self.assertEqual(t2["status"], "queued")
            self.assertFalse(raw.exists())
            self.assertFalse(delivery2.exists())

    def test_human_reject_pending_review_moves_to_failed_samples(self):
        from desktop.backend.queue_ops import can_human_reject, human_reject_passed

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = root / "archive" / "pending-review" / "042-demo-c01"
            gold = held / "questions" / "q01" / "data" / "gold.json"
            gold.parent.mkdir(parents=True)
            gold.write_text("{}", encoding="utf-8")
            (held / "sample_qc.md").write_text("# qc\n", encoding="utf-8")
            (held / "PENDING_REVIEW.md").write_text("# pending\n", encoding="utf-8")
            pack = root / "materials" / "example" / "demo-pack"
            pack.mkdir(parents=True)
            (pack / "CATALOG.json").write_text(
                json.dumps({"pack": "demo-pack", "status": "GATE_FAILED_042"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (pack.parent / "CATALOG.json").write_text(
                json.dumps(
                    {
                        "domain": "example",
                        "packs": [{"pack": "demo-pack", "status": "GATE_FAILED_042"}],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            queue_path = root / "queue" / "queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "tasks": [
                            {
                                "id": "t-zero",
                                "slug": "demo",
                                "status": "blocked",
                                "review_status": "manual_review",
                                "avg_accuracy": 0.0,
                                "sample_dir": "work/samples/042-demo",
                                "materials_pack": "materials/example/demo-pack",
                                "history": [],
                                "candidate_results": [
                                    {
                                        "status": "manual_review",
                                        "avg_accuracy": 0.0,
                                        "sample_dir": "archive/pending-review/042-demo-c01",
                                        "candidate_dir": "work/samples/042-demo/work/candidates/candidate-01",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            task = json.loads(queue_path.read_text(encoding="utf-8"))["tasks"][0]
            self.assertTrue(can_human_reject(task))
            result = human_reject_passed(root, task_id="t-zero", reason="题坏", requeue=False)
            self.assertTrue(result["ok"])
            self.assertFalse(held.exists())
            dest = root / "archive" / "failed-samples" / "042-demo-c01"
            self.assertTrue((dest / "HUMAN_REJECT.md").is_file())
            self.assertFalse((dest / "PENDING_REVIEW.md").is_file())
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            row = data["tasks"][0]
            self.assertEqual(row["status"], "gate_failed")
            self.assertEqual(row["review_status"], "human_reject")
            self.assertEqual(row["candidate_results"][0]["sample_dir"], "archive/failed-samples/042-demo-c01")

    def test_set_tasks_status_collects_errors(self):
        from desktop.backend import queue_ops

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue_path = root / "queue" / "queue.json"
            queue_path.parent.mkdir(parents=True)
            queue_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "tasks": [
                            {
                                "id": "t-ok",
                                "status": "cancelled",
                                "history": [],
                                "materials_pack": None,
                                "sample_dir": None,
                            },
                            {
                                "id": "t-acc",
                                "status": "gate_failed",
                                "avg_accuracy": 0.75,
                                "history": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_qw(_workspace, args):
                task_id = args[args.index("--task-id") + 1]
                status = args[args.index("--status") + 1]
                data = json.loads(queue_path.read_text(encoding="utf-8"))
                for task in data["tasks"]:
                    if task["id"] == task_id:
                        task["status"] = status
                        task["history"].append({"event": "status", "detail": f"→ {status}"})
                        return task
                raise RuntimeError("missing")

            with patch.object(queue_ops, "_qw", side_effect=fake_qw):
                result = queue_ops.set_tasks_status(
                    root,
                    task_ids=["t-ok", "t-acc", "t-missing"],
                    status="queued",
                    reason="batch",
                )
            self.assertEqual(result["updated"], ["t-ok"])
            self.assertEqual(len(result["errors"]), 2)
            data = json.loads(queue_path.read_text(encoding="utf-8"))
            self.assertEqual(next(t for t in data["tasks"] if t["id"] == "t-ok")["status"], "queued")
            self.assertEqual(next(t for t in data["tasks"] if t["id"] == "t-acc")["status"], "gate_failed")


class StageProgressTests(unittest.TestCase):
    def test_emit_event_accumulates_done_steps(self):
        from desktop.backend import run_control

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "queue").mkdir()
            with patch.object(run_control, "queue_dir", lambda r: Path(directory) / "queue"):
                run_control.emit_event(root, step="generate", status="started", task_id="t-1", slug="demo", detail="a1 · 调用出题模型")
                run_control.emit_event(
                    root,
                    step="generate",
                    status="finished",
                    task_id="t-1",
                    slug="demo",
                    detail="a1 · 证据校验",
                    extra={"attempt": 1, "candidate_id": "candidate-01"},
                )
                run_control.emit_event(root, step="precheck", status="started", task_id="t-1", slug="demo")
                run_control.emit_event(root, step="precheck", status="finished", task_id="t-1", slug="demo")
                prog = run_control.read_progress(root)
                row = prog["tasks"]["t-1"]
                self.assertEqual(row["done_steps"], ["generate", "precheck"])
                self.assertEqual(row["step"], "precheck")
                self.assertEqual(row["attempt"], 1)
                self.assertEqual(row["candidate_id"], "candidate-01")

    def test_generate_and_judge_progress_details(self):
        from desktop.backend.runner import _generate_progress_detail, _judge_progress_detail

        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            self.assertEqual(_generate_progress_detail(raw, attempt=1, call_n=1), "a1 · 调用出题模型")
            (raw / "generate_qa.json").write_text("{}", encoding="utf-8")
            self.assertEqual(_generate_progress_detail(raw, attempt=1, call_n=1), "a1 · 解析题目")
            (raw / "qa_validate.json").write_text('{"ok": true}', encoding="utf-8")
            self.assertEqual(_generate_progress_detail(raw, attempt=2, call_n=2), "a2 · 重试2 · 证据校验")

            self.assertEqual(_judge_progress_detail(raw), "0/8")
            (raw / "judge_1.json").write_text('{"correct": true}', encoding="utf-8")
            (raw / "judge_2.json").write_text('{"correct": false}', encoding="utf-8")
            self.assertEqual(_judge_progress_detail(raw), "2/8 · 已对 1")


def _write_pending_review_task(root: Path, *, task_id: str = "t-zero") -> Path:
    held = root / "archive" / "pending-review" / "042-demo-c01"
    gold = held / "questions" / "q01" / "data" / "gold.json"
    gold.parent.mkdir(parents=True)
    gold.write_text(
        json.dumps(
            {
                "question": "What portal is used?",
                "answer": "the overflowing basin; the mirror",
                "evidence": [{"id": "E1", "text": "the overflowing basin"}],
                "evidence_reasoning": "E1 names the basin.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (held / "main_file").mkdir(parents=True, exist_ok=True)
    (held / "main_file" / "context_000001.txt").write_text(
        "the overflowing basin appears in the bedroom. later the mirror is the crossing.",
        encoding="utf-8",
    )
    (held / "sample_qc.md").write_text("# qc\n", encoding="utf-8")
    (held / "PENDING_REVIEW.md").write_text("# pending\n", encoding="utf-8")
    cand = root / "work" / "samples" / "042-demo" / "work" / "candidates" / "candidate-01"
    (cand / "work" / "raw").mkdir(parents=True)
    (cand / "work" / "raw" / "judge_summary.json").write_text(
        json.dumps({"avg_accuracy": 0.0, "n": 8, "correct": 0}),
        encoding="utf-8",
    )
    pack = root / "materials" / "example" / "demo-pack"
    pack.mkdir(parents=True)
    (pack / "CATALOG.json").write_text(
        json.dumps({"pack": "demo-pack", "status": "GATE_FAILED_042", "docs": []}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (pack.parent / "CATALOG.json").write_text(
        json.dumps(
            {"domain": "example", "domain_key": "example", "packs": [{"pack": "demo-pack", "status": "GATE_FAILED_042"}]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    queue_path = root / "queue" / "queue.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(
        json.dumps(
            {
                "version": 2,
                "tasks": [
                    {
                        "id": task_id,
                        "slug": "demo",
                        "status": "blocked",
                        "review_status": "manual_review",
                        "avg_accuracy": 0.0,
                        "sample_dir": "work/samples/042-demo",
                        "materials_pack": "materials/example/demo-pack",
                        "history": [],
                        "candidate_results": [
                            {
                                "status": "manual_review",
                                "avg_accuracy": 0.0,
                                "sample_dir": "archive/pending-review/042-demo-c01",
                                "candidate_dir": "work/samples/042-demo/work/candidates/candidate-01",
                                "candidate_id": "candidate-01",
                                "candidate_index": 1,
                                "attempt": 1,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return held


class AutoReviewTests(unittest.TestCase):
    def test_install_ca_bundle_sets_ssl_cert_file(self) -> None:
        from desktop.backend.llm_client import install_ca_bundle

        old = os.environ.pop("SSL_CERT_FILE", None)
        try:
            path = install_ca_bundle(force=True)
            self.assertTrue(path)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(os.environ.get("SSL_CERT_FILE"), path)
        finally:
            if old is None:
                os.environ.pop("SSL_CERT_FILE", None)
            else:
                os.environ["SSL_CERT_FILE"] = old

    def test_extract_json_object_from_fence(self) -> None:
        from desktop.backend.llm_client import extract_json_object

        data = extract_json_object('sure\n```json\n{"verdict": "pass"}\n```\n')
        self.assertEqual(data["verdict"], "pass")

    def test_auto_review_fail_moves_to_failed_samples(self) -> None:
        from desktop.backend import auto_review as auto_review_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = _write_pending_review_task(root)
            with patch.object(auto_review_mod, "load_keys"), patch.object(
                auto_review_mod,
                "chat_json",
                return_value={
                    "verdict": "fail",
                    "gold_supported": False,
                    "question_ok": False,
                    "reason": "金标与原文不符",
                },
            ):
                result = auto_review_mod.run_auto_review(root, "t-zero")
            self.assertTrue(result["ok"])
            self.assertEqual(result["auto_review"]["action"], "rejected")
            self.assertFalse(held.exists())
            dest = root / "archive" / "failed-samples" / "042-demo-c01"
            self.assertTrue((dest / "HUMAN_REJECT.md").is_file())
            self.assertTrue((dest / "questions" / "q01" / "data" / "auto_review.json").is_file())
            task = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(task["status"], "gate_failed")
            self.assertEqual(task["auto_review"]["verdict"], "fail")
            self.assertIn("auto-review", task["failure_reason"])

    def test_auto_review_invalid_json_aborts_without_moving(self) -> None:
        from desktop.backend import auto_review as auto_review_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = _write_pending_review_task(root)
            with patch.object(auto_review_mod, "load_keys"), patch.object(
                auto_review_mod, "chat_json", return_value={"verdict": "maybe"}
            ):
                result = auto_review_mod.run_auto_review(root, "t-zero")
            self.assertFalse(result["ok"])
            self.assertTrue(result.get("aborted"))
            self.assertTrue(held.exists())
            task = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(task["auto_review"]["action"], "aborted")

    def test_auto_review_pass_calls_review_pass(self) -> None:
        from desktop.backend import auto_review as auto_review_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pending_review_task(root)
            with patch.object(auto_review_mod, "load_keys"), patch.object(
                auto_review_mod,
                "chat_json",
                return_value={
                    "verdict": "pass",
                    "gold_supported": True,
                    "question_ok": True,
                    "reason": "金标可由证据推出",
                },
            ), patch.object(auto_review_mod, "run_review_pass", return_value={"ok": True, "task_id": "t-zero"}) as mocked:
                result = auto_review_mod.run_auto_review(root, "t-zero")
            mocked.assert_called_once()
            self.assertTrue(result["ok"])
            self.assertEqual(result["auto_review"]["action"], "promoted")
            task = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(task["auto_review"]["verdict"], "pass")
            self.assertEqual(task["status"], "blocked")

    def test_auto_review_false_negatives_in_band_rescores(self) -> None:
        from desktop.backend import auto_review as auto_review_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_pending_review_task(root)
            with patch.object(auto_review_mod, "load_keys"), patch.object(
                auto_review_mod,
                "chat_json",
                return_value={
                    "verdict": "pass",
                    "gold_supported": True,
                    "question_ok": True,
                    "false_negative_rollouts": ["rollout_01", "rollout_02"],
                    "rescored_correct_count": 2,
                    "reason": "两条同义表述被原 Judge 误判",
                },
            ), patch.object(auto_review_mod, "run_review_pass", return_value={"ok": True, "task_id": "t-zero"}) as mocked:
                result = auto_review_mod.run_auto_review(root, "t-zero")
            mocked.assert_called_once()
            kwargs = mocked.call_args.kwargs
            self.assertFalse(kwargs.get("require_zero"))
            self.assertFalse(kwargs.get("zero_rechecked"))
            self.assertEqual(result["auto_review"]["action"], "rescored")
            self.assertEqual(result["auto_review"]["rescored_correct_count"], 2)
            self.assertEqual(result["auto_review"]["rescored_avg_accuracy"], 0.25)
            judge = json.loads(
                (root / "work" / "samples" / "042-demo" / "work" / "candidates" / "candidate-01" / "work" / "raw" / "judge_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(judge["avg_accuracy"], 0.25)
            self.assertEqual(judge["correct_count"], 2)

    def test_auto_review_false_negatives_too_easy_rejects(self) -> None:
        from desktop.backend import auto_review as auto_review_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            held = _write_pending_review_task(root)
            with patch.object(auto_review_mod, "load_keys"), patch.object(
                auto_review_mod,
                "chat_json",
                return_value={
                    "verdict": "pass",
                    "gold_supported": True,
                    "question_ok": True,
                    "false_negative_rollouts": [f"rollout_0{i}" for i in range(1, 6)],
                    "rescored_correct_count": 5,
                    "reason": "五条实质已答对",
                },
            ), patch.object(auto_review_mod, "run_review_pass") as promote:
                result = auto_review_mod.run_auto_review(root, "t-zero")
            promote.assert_not_called()
            self.assertEqual(result["auto_review"]["action"], "rejected")
            self.assertFalse(held.exists())
            task = json.loads((root / "queue" / "queue.json").read_text(encoding="utf-8"))["tasks"][0]
            self.assertEqual(task["status"], "gate_failed")

    def test_normalize_rollout_ids_and_rescore(self) -> None:
        from desktop.backend.auto_review import apply_false_negative_rescore, normalize_rollout_ids

        self.assertEqual(normalize_rollout_ids(["rollout_01", "2", 8, "rollout_01", 0, 9]), [1, 2, 8])
        patched = apply_false_negative_rescore({"avg_accuracy": 0.0, "correct_count": 0}, [1, 2])
        self.assertEqual(patched["correct_count"], 2)
        self.assertEqual(patched["avg_accuracy"], 0.25)
        self.assertTrue(patched["scores"][0]["rescored_from_false_negative"])


class MaterialAuditTests(unittest.TestCase):
    def _write_pack(self, root: Path, pack_name: str) -> Path:
        pack = root / "materials" / "example" / pack_name
        pack.mkdir(parents=True)
        md = pack / "md" / "doc.md"
        md.parent.mkdir(parents=True)
        md.write_text("# Niche protocol\n\nCross-document exception clause lives here.\n", encoding="utf-8")
        catalog_path = pack / "CATALOG.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "pack": pack_name,
                    "domain": "example",
                    "domain_key": "example",
                    "status": "READY",
                    "coldness": "cold",
                    "theme": "niche protocol exceptions",
                    "docs": [
                        {
                            "doc_id": "DOC1",
                            "title": "Niche protocol",
                            "file_md": f"materials/example/{pack_name}/md/doc.md",
                            "approx_tokens": 20000,
                            "license_note": "public domain",
                        }
                    ],
                    "total_approx_tokens": 20000,
                    "enough_for_16k": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return catalog_path

    def _write_domain(self, root: Path, packs: list[str]) -> None:
        domain = root / "materials" / "example"
        domain.mkdir(parents=True, exist_ok=True)
        (domain / "CATALOG.json").write_text(
            json.dumps(
                {
                    "domain": "example",
                    "domain_key": "example",
                    "packs": [{"pack": name, "path": str(root / "materials" / "example" / name)} for name in packs],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_audit_writes_llm_audit_without_changing_status(self) -> None:
        from desktop.backend import material_audit as audit_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = self._write_pack(root, "demo-pack")
            self._write_domain(root, ["demo-pack"])
            with patch.object(
                audit_mod,
                "chat_json",
                return_value={
                    "status": "pass",
                    "summary": "冷源且足够长",
                    "checks": {
                        "license_ok": True,
                        "enough_length": True,
                        "long_context_potential": True,
                        "cold_enough": True,
                    },
                    "notes": "ok",
                },
            ):
                result = audit_mod.audit_pack(root, "example", "demo-pack")
            self.assertTrue(result["ok"])
            saved = json.loads(catalog_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "READY")
            self.assertEqual(saved["llm_audit"]["status"], "pass")
            self.assertEqual(saved["llm_audit"]["summary"], "冷源且足够长")
            self.assertEqual(saved["theme"], "niche protocol exceptions")

    def test_audit_packs_stops_remaining(self) -> None:
        from desktop.backend import material_audit as audit_mod

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._write_pack(root, "pack-a")
            second = self._write_pack(root, "pack-b")
            self._write_domain(root, ["pack-a", "pack-b"])
            seen = {"count": 0}

            def fake_chat_json(*_args, **_kwargs):
                seen["count"] += 1
                return {
                    "status": "pass",
                    "summary": f"pack {seen['count']}",
                    "checks": {
                        "license_ok": True,
                        "enough_length": True,
                        "long_context_potential": True,
                        "cold_enough": True,
                    },
                    "notes": "ok",
                }

            def should_stop() -> bool:
                return seen["count"] >= 1

            with patch.object(audit_mod, "chat_json", side_effect=fake_chat_json):
                result = audit_mod.audit_packs(
                    root,
                    [
                        {"domain_key": "example", "pack": "pack-a"},
                        {"domain_key": "example", "pack": "pack-b"},
                    ],
                    should_stop=should_stop,
                )

            self.assertTrue(result["stopped"])
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["skipped"], 1)
            self.assertFalse(result["ok"])
            self.assertEqual(len(result["results"]), 2)
            self.assertTrue(result["results"][0]["ok"])
            self.assertTrue(result["results"][1]["stopped"])
            self.assertEqual(result["results"][1]["pack"], "pack-b")
            self.assertEqual(seen["count"], 1)
            saved_first = json.loads(first.read_text(encoding="utf-8"))
            saved_second = json.loads(second.read_text(encoding="utf-8"))
            self.assertEqual(saved_first["llm_audit"]["status"], "pass")
            self.assertNotIn("llm_audit", saved_second)


if __name__ == "__main__":
    unittest.main()
