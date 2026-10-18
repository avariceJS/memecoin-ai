"""Synthetic PNG/receipt fixtures: no browser or visual quality claim."""

import copy
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "scripts"))
import visual_gate as gate  # noqa: E402
import workflow  # noqa: E402


def png(width, height, color=0):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
        + chunk(
            b"IDAT",
            zlib.compress((b"\0" + b"\x10" * width * {0: 1, 2: 3, 4: 2, 6: 4}[color]) * height),
        )
        + chunk(b"IEND", b"")
    )


class VisualGateTests(unittest.TestCase):
    def setUp(self):
        local = APP / ".local"
        local.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=local)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.digest = "a" * 64
        self.route = "/"
        self.prefix = gate.ARTIFACTS + "fixture/"
        self.report_path = self.prefix + "review.json"
        snapshot = self.root / "apps/control-center/published/observatory-v1.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(b'{"synthetic_fixture":true}')
        before = self.make_capture("first", (1600, 1000), "viewport", after=False)
        captures = [self.make_capture(str(w), (w, h), "viewport") for w, h in sorted(gate.VIEWS)]
        captures.append(self.make_capture("full", (1600, 1000), "fullpage"))
        self.receipt = {
            "schema_version": 1,
            "task_id": "CC02.2",
            "acceptance_scope": "page",
            "web_sha256": self.digest,
            "snapshot_sha256": gate.sha(snapshot.read_bytes()),
            "reference_sha256": gate.REFERENCE_SHA,
            "builder_id": "fixture-builder",
            "reviewer_id": "fixture-reviewer",
            "model_binding": "UNVERIFIED",
            "first_review": "fixture: no claim of real visual review",
            "fix_summary": "fixture: test gate mechanics only",
            "remaining_findings": [],
            "screens": [
                {
                    "route": "/",
                    "rubric_scope": "page",
                    "before": before,
                    "after": captures,
                    "rubric": {
                        key: {"score": 5, "reason": "synthetic fixture"} for key in gate.AXES
                    },
                    "remaining_weaknesses": "Not an application screenshot; no aesthetic evidence",
                }
            ],
        }

    def put(self, path, data):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return {"path": path, "sha256": gate.sha(data)}

    def make_capture(self, name, viewport, kind, after=True):
        item = self.put(self.prefix + name + ".png", png(*viewport))
        item.update(
            route="/",
            capture_id=name,
            viewport=list(viewport),
            capture=kind,
            dpr=1,
            zoom=1,
            web_sha256=self.digest,
            captured_at="2026-10-04T00:00:00Z",
            browser="fixture",
        )
        if after:
            report = dict(
                source="browser-dom",
                acceptance_scope="page",
                capture_id=name,
                screenshot_sha256=item["sha256"],
                page_findings=[],
                route="/",
                viewport=list(viewport),
                web_sha256=self.digest,
                page_overflow_px=0,
                text_overlap_count=0,
                card_escape_count=0,
                unintended_clip_count=0,
                page_overflow_hidden=False,
                allowed_scroll_containers=[],
                inspected_regions=sorted(gate.SHELL_REGIONS | gate.PAGE_REGIONS["/"]),
            )
            item["layout_report"] = self.put(
                self.prefix + name + ".json", json.dumps(report).encode()
            )
        return item

    def check(self):
        self.put(self.report_path, json.dumps(self.receipt).encode())
        return gate.validate_receipt(
            self.report_path, self.receipt["task_id"], self.digest, self.root
        )

    def test_valid_artifact_matrix_with_unverified_model(self):
        self.check()

    def test_missing_or_modified_screenshot(self):
        item = self.receipt["screens"][0]["before"]
        file = self.root / item["path"]
        file.unlink()
        with self.assertRaises(OSError):
            self.check()
        file.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "SHA"):
            self.check()

    def test_fake_crop_wrong_size_and_missing_viewport(self):
        original = copy.deepcopy(self.receipt)
        self.receipt["screens"][0]["before"]["capture"] = "crop"
        with self.assertRaisesRegex(ValueError, "Cropped"):
            self.check()
        self.receipt = copy.deepcopy(original)
        self.receipt["screens"][0]["before"]["viewport"] = [1280, 800]
        with self.assertRaisesRegex(ValueError, "width mismatch"):
            self.check()
        self.receipt = original
        self.receipt["screens"][0]["after"].pop()
        with self.assertRaisesRegex(ValueError, "matrix"):
            self.check()

    def test_wrong_route_stale_source_and_self_review(self):
        original = copy.deepcopy(self.receipt)
        self.receipt["screens"][0]["route"] = "/calendar"
        with self.assertRaisesRegex(ValueError, "route coverage"):
            self.check()
        self.receipt = copy.deepcopy(original)
        self.receipt["web_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.check()
        self.receipt = original
        self.receipt["reviewer_id"] = self.receipt["builder_id"]
        with self.assertRaisesRegex(ValueError, "Independent"):
            self.check()

    def test_layout_failure_and_hidden_overflow_rejected(self):
        item = self.receipt["screens"][0]["after"][0]
        link = item["layout_report"]
        original = json.loads((self.root / link["path"]).read_text())
        for key, value in [
            ("page_overflow_px", 2),
            ("text_overlap_count", 1),
            ("card_escape_count", 1),
            ("page_overflow_hidden", True),
        ]:
            report = dict(original, **{key: value})
            item["layout_report"] = self.put(link["path"], json.dumps(report).encode())
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check()

    def test_mediocre_score_or_major_findings_rejected(self):
        self.receipt["screens"][0]["rubric"]["typography"]["score"] = 3
        with self.assertRaisesRegex(ValueError, "quality"):
            self.check()
        self.receipt["screens"][0]["rubric"]["typography"]["score"] = 5
        self.receipt["remaining_findings"] = [
            {"severity": "major", "reason": "overflow", "owner": "fixture"}
        ]
        with self.assertRaisesRegex(ValueError, "major"):
            self.check()

    def test_path_and_symlink_denied_before_target_read(self):
        for path in ["work/holdout.json", self.prefix + "../x", "/etc/passwd"]:
            with self.assertRaises(ValueError):
                gate.read_owned(self.root, path, artifact=True)
        image = self.receipt["screens"][0]["before"]["path"]
        file = self.root / image
        file.unlink()
        file.symlink_to(self.root / "must-never-open")
        with self.assertRaises(OSError):
            self.check()

    def test_full_final_matrix_requires_final_browser_checks(self):
        self.receipt["task_id"] = "CC02.8"
        self.receipt["acceptance_scope"] = "integrated"
        template = self.receipt["screens"][0]
        screens = []
        for index, route in enumerate(sorted(gate.ROUTES)):
            screen = copy.deepcopy(template)
            screen["route"] = route
            screen["rubric_scope"] = "integrated"
            screen["before"]["route"] = route
            screen["before"]["capture_id"] = route + ":first"
            for item in screen["after"]:
                item["route"] = route
                item["capture_id"] = route + ":" + item["capture_id"]
                report = json.loads((self.root / item["layout_report"]["path"]).read_text())
                report["route"] = route
                report["capture_id"] = item["capture_id"]
                report["acceptance_scope"] = "integrated"
                report["inspected_regions"] = sorted(gate.SHELL_REGIONS | gate.PAGE_REGIONS[route])
                item["layout_report"] = self.put(
                    self.prefix + str(index) + "/" + Path(item["path"]).stem + ".json",
                    json.dumps(report).encode(),
                )
            screens.append(screen)
        self.receipt["screens"] = screens
        self.put(self.report_path, json.dumps(self.receipt).encode())
        with self.assertRaisesRegex(ValueError, "Final browser"):
            gate.validate_receipt(self.report_path, "CC02.8", self.digest, self.root)
        self.receipt["final_checks"] = {
            kind: dict(
                result="PASS",
                evidence=self.put(
                    self.prefix + kind + ".json", json.dumps(self.final_envelope(kind)).encode()
                ),
            )
            for kind in gate.FINAL_CHECKS
        }
        self.check()
        # Shell task accepts honest old page defects without declaring pages accepted.
        self.receipt.update(task_id="CC02.1", acceptance_scope="shell")
        for screen in screens:
            screen["rubric_scope"] = "shell"
            for item in screen["after"]:
                link = item["layout_report"]
                report = json.loads((self.root / link["path"]).read_text())
                report.update(
                    acceptance_scope="shell",
                    text_overlap_count=2,
                    shell_counts=dict(overflow_px=0, **{f: 0 for f in gate.COUNT_FIELDS}),
                )
                report["page_findings"] = [
                    dict(
                        route=screen["route"],
                        metric="text_overlap_count",
                        owner_task=gate.PAGE_OWNERS[screen["route"]],
                        severity="major",
                        reason="fixture old page overlap",
                    )
                ]
                item["layout_report"] = self.put(link["path"], json.dumps(report).encode())
        self.check()

    def test_scope_and_capture_reuse_rejected(self):
        self.receipt["acceptance_scope"] = "shell"
        with self.assertRaisesRegex(ValueError, "acceptance scope"):
            self.check()
        self.receipt["acceptance_scope"] = "page"
        screen = self.receipt["screens"][0]
        screen["rubric_scope"] = "shell"
        with self.assertRaisesRegex(ValueError, "Rubric"):
            self.check()
        screen["rubric_scope"] = "page"
        screen["after"][0]["capture_id"] = screen["before"]["capture_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate capture"):
            self.check()

    def test_png_truncation_crc_pixels_and_decode_budget(self):
        good = png(10, 10)
        self.assertEqual(gate.png_dimensions(good), (10, 10))
        for color in (2, 4, 6):
            self.assertEqual(gate.png_dimensions(png(10, 10, color)), (10, 10))
        for bad in [
            good[:24],
            good[:-12],
            good + b"extra",
            good[:40] + bytes([good[40] ^ 1]) + good[41:],
        ]:
            with self.subTest(length=len(bad)), self.assertRaises(ValueError):
                gate.png_dimensions(bad)

        # Valid CRC but intentionally wrong decoded scanline length.
        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data))
            )

        for width, height, pixels in [(10, 10, b"x"), (32768, 32768, b"x"), (1, 1, b"\x05\x01")]:
            bad = (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(pixels))
                + chunk(b"IEND", b"")
            )
            with self.assertRaises(ValueError):
                gate.png_dimensions(bad)

    def test_capture_report_binding_and_mandatory_regions(self):
        item = self.receipt["screens"][0]["after"][0]
        link = item["layout_report"]
        original = json.loads((self.root / link["path"]).read_text())
        for field, value in [
            ("capture_id", "other"),
            ("screenshot_sha256", "0" * 64),
            ("route", "/calendar"),
            ("inspected_regions", ["anything"]),
            ("acceptance_scope", "shell"),
        ]:
            item["layout_report"] = self.put(
                link["path"], json.dumps(dict(original, **{field: value})).encode()
            )
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check()
        item["layout_report"] = self.put(link["path"], json.dumps(original).encode())
        item["route"] = "/models"
        with self.assertRaisesRegex(ValueError, "route/id"):
            self.check()

    def test_shell_preserves_page_defects_and_requires_owned_findings(self):
        item = self.receipt["screens"][0]["after"][0]
        link = item["layout_report"]
        report = json.loads((self.root / link["path"]).read_text())
        report.update(
            acceptance_scope="shell",
            inspected_regions=sorted(gate.SHELL_REGIONS),
            text_overlap_count=3,
            shell_counts=dict(overflow_px=0, **{f: 0 for f in gate.COUNT_FIELDS}),
        )
        report["page_findings"] = [
            {
                "route": "/",
                "metric": "text_overlap_count",
                "owner_task": "CC02.2",
                "severity": "major",
                "reason": "fixture old page overlap",
            }
        ]
        gate.layout(report, item, "/", "shell", self.digest)
        with self.assertRaises(ValueError):
            gate.layout(report, item, "/", "page", self.digest)
        report["page_findings"] = []
        with self.assertRaisesRegex(ValueError, "missing from findings"):
            gate.layout(report, item, "/", "shell", self.digest)
        report["text_overlap_count"] = 0
        report["shell_counts"]["card_escape_count"] = 1
        with self.assertRaisesRegex(ValueError, "Shell layout defect"):
            gate.layout(report, item, "/", "shell", self.digest)

    def final_envelope(self, kind):
        return dict(
            schema_version=1,
            kind=kind,
            result="PASS",
            web_sha256=self.digest,
            snapshot_sha256=self.receipt["snapshot_sha256"],
            executed_at="2026-10-04T00:00:00Z",
            command="fixture only",
            raw_evidence=self.put(
                self.prefix + "raw.log", b"synthetic fixture, not actual browser evidence"
            ),
            routes=sorted(gate.ROUTES),
            browser="fixture",
            zoom=2,
            zoom_method="browser",
            prefers_reduced_motion="reduce",
            interactions=["Tab", "Arrow", "Escape", "focus-return"],
            disallowed_request_count=0,
            exit_code=0,
            reviewer_id="fixture-reviewer",
        )

    def test_final_envelopes_current_kind_routes_zoom_and_raw_evidence(self):
        for kind in gate.FINAL_CHECKS:
            envelope = self.final_envelope(kind)
            check = dict(
                result="PASS",
                evidence=self.put(self.prefix + "final.json", json.dumps(envelope).encode()),
            )
            gate.final_check(kind, check, self.digest, self.receipt["snapshot_sha256"], self.root)
        original = self.final_envelope("zoom200")
        for key, value in [
            ("kind", "build"),
            ("web_sha256", "b" * 64),
            ("snapshot_sha256", "b" * 64),
            ("routes", ["/"]),
            ("zoom_method", "css"),
            ("zoom", 1),
            ("executed_at", "2026-10-04T00:00:00"),
            ("raw_evidence", {"path": self.prefix + "missing.log", "sha256": "a" * 64}),
        ]:
            envelope = dict(original, **{key: value})
            check = dict(
                result="PASS",
                evidence=self.put(self.prefix + "final.json", json.dumps(envelope).encode()),
            )
            with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                gate.final_check(
                    "zoom200", check, self.digest, self.receipt["snapshot_sha256"], self.root
                )
        check = dict(result="PASS", evidence=self.put(self.prefix + "not-report.png", png(1, 1)))
        with self.assertRaises((ValueError, UnicodeError)):
            gate.final_check(
                "zoom200", check, self.digest, self.receipt["snapshot_sha256"], self.root
            )

    def test_source_digest_changes_and_symlink_root_denied(self):
        base = self.root / "apps/control-center/web"
        (base / "src").mkdir(parents=True)
        for name in [
            "package.json",
            "package-lock.json",
            "vite.config.ts",
            "tsconfig.json",
            "index.html",
        ]:
            (base / name).write_text("fixture")
        (base / "src/app.tsx").write_text("before")
        initial = gate.web_digest(self.root)
        (base / "src/app.tsx").write_text("after")
        self.assertNotEqual(initial, gate.web_digest(self.root))
        (base / "public").symlink_to(self.root / "unapproved")
        with self.assertRaisesRegex(ValueError, "symlink"):
            gate.web_digest(self.root)

    def test_workflow_cannot_remove_cc02_reviews_or_tasks(self):
        state = json.loads((APP / "memory/WORK_STATE.json").read_text())
        state["tasks"][1]["required_reviews"] = ["boundary"]
        with self.assertRaisesRegex(ValueError, "reviews removed"):
            workflow.validate(state)
        state = json.loads((APP / "memory/WORK_STATE.json").read_text())
        state["tasks"].pop()
        with self.assertRaisesRegex(ValueError, "coverage"):
            workflow.validate(state)


if __name__ == "__main__":
    unittest.main()
