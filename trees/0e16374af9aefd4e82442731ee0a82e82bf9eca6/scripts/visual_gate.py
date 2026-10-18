"""CC02 developer-only artifact conformance. Does not judge aesthetics or run a browser."""

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import zlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = "docs/control-center/references/cc02-final/"
REFERENCE_SHA = "dd03716ed059c4bdf85726489460d4c07acb6836a872553ccf2157bb8dd014d6"
VIEWS = {(1440, 900), (1600, 1000), (1920, 1080), (1024, 768)}
ROUTES = {"/", "/calendar", "/research", "/models", "/sources", "/system"}
TASK_ROUTES = {
    "CC02.0": set(),
    "CC02.1": ROUTES,
    "CC02.2": {"/"},
    "CC02.3": {"/calendar"},
    "CC02.4": {"/research"},
    "CC02.5": {"/models"},
    "CC02.6": {"/sources"},
    "CC02.7": {"/system"},
    "CC02.8": ROUTES,
}
AXES = {
    "composition",
    "hierarchy",
    "density",
    "typography",
    "geometry",
    "navigation",
    "identity",
    "reference_family",
    "state_clarity",
    "interaction",
}

SHELL_REGIONS = {"sidebar", "header", "main-grid", "shell-containment"}
PAGE_REGIONS = {
    "/": {"overview-kpis", "overview-chart", "capability-cards"},
    "/calendar": {"d48-grid", "window-details"},
    "/research": {"research-gates"},
    "/models": {"model-summary", "benchmark-comparison"},
    "/sources": {"source-blockers", "pool-ids"},
    "/system": {"capability-cards"},
}
PAGE_OWNERS = {
    next(iter(TASK_ROUTES[t])): t for t in TASK_ROUTES if t not in {"CC02.0", "CC02.1", "CC02.8"}
}
COUNT_FIELDS = ("text_overlap_count", "card_escape_count", "unintended_clip_count")
FINAL_CHECKS = {
    "zoom200",
    "keyboard",
    "reduced_motion",
    "network_boundary",
    "semantic_tests",
    "build",
    "boundary_review",
}


def timestamp(value):
    require(isinstance(value, str), "Timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(
        parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0,
        "UTC timestamp required",
    )


def png_dimensions(data):
    """Bounded PNG structure, CRC and pixel-stream validation (8-bit noninterlaced)."""
    require(
        len(data) <= 16 * 1024 * 1024 and data[:8] == b"\x89PNG\r\n\x1a\n", "Expected bounded PNG"
    )
    offset, width, height, stride = 8, None, None, None
    compressed = bytearray()
    seen_idat = ended_idat = ended = seen_palette = False
    while offset < len(data):
        require(offset + 12 <= len(data), "Truncated PNG chunk")
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + size
        require(end <= len(data), "Truncated PNG payload")
        payload = data[offset + 8 : end - 4]
        require(re.fullmatch(rb"[A-Za-z]{4}", kind) and not (kind[2] & 32), "PNG chunk type")
        require(
            zlib.crc32(kind + payload) == struct.unpack(">I", data[end - 4 : end])[0], "PNG CRC"
        )
        if width is None:
            require(kind == b"IHDR" and size == 13, "PNG IHDR required")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            require(0 < width <= 32768 and 0 < height <= 32768, "PNG dimensions limit")
            require(
                depth == 8
                and color in {0, 2, 4, 6}
                and (compression, filtering, interlace) == (0, 0, 0),
                "Unsupported PNG format",
            )
            stride = 1 + width * {0: 1, 2: 3, 4: 2, 6: 4}[color]
            require(stride * height <= 128 * 1024 * 1024, "PNG decoded size limit")
        elif kind == b"IHDR":
            raise ValueError("Duplicate PNG IHDR")
        elif kind == b"IDAT":
            require(not ended_idat, "Nonconsecutive PNG IDAT")
            seen_idat = True
            compressed.extend(payload)
        elif kind == b"IEND":
            require(size == 0 and seen_idat and end == len(data), "Invalid PNG IEND")
            ended = True
        elif kind == b"PLTE":
            require(
                not seen_palette
                and not seen_idat
                and color in {2, 6}
                and 0 < size <= 768
                and size % 3 == 0,
                "Invalid PNG palette",
            )
            seen_palette = True
        else:
            require(kind[0] & 32, "Unsupported critical PNG chunk")
        if seen_idat and kind != b"IDAT":
            ended_idat = True
        offset = end
    require(ended and compressed, "PNG incomplete")
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, stride * height + 1)
    except zlib.error as exc:
        raise ValueError("Invalid PNG compressed stream") from exc
    require(
        len(pixels) == stride * height
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail,
        "PNG pixel stream size/end",
    )
    require(all(pixels[i] <= 4 for i in range(0, len(pixels), stride)), "PNG row filter")
    return width, height


def layout(report, item, route, scope, expected_digest):
    require(report["source"] == "browser-dom" and report["route"] == route, "Layout source/route")
    require(
        report["capture_id"] == item["capture_id"]
        and report["screenshot_sha256"] == item["sha256"],
        "Layout capture binding",
    )
    require(
        tuple(report["viewport"]) == tuple(item["viewport"])
        and report["web_sha256"] == expected_digest,
        "Stale layout report",
    )
    require(report["acceptance_scope"] == scope, "Layout acceptance scope")
    require(
        type(report["page_overflow_px"]) in {int, float}
        and 0 <= report["page_overflow_px"] < float("inf"),
        "Invalid page overflow",
    )
    for field in COUNT_FIELDS:
        require(type(report[field]) is int and report[field] >= 0, "Invalid layout count")
    require(report["page_overflow_hidden"] is False, "Overflow hidden on page")
    require(isinstance(report["allowed_scroll_containers"], list), "Scroll regions absent")
    regions = set(report["inspected_regions"])
    require(SHELL_REGIONS <= regions, "Shell regions absent")
    findings = report["page_findings"]
    if scope == "shell":
        shell = report["shell_counts"]
        require(
            0 <= shell["overflow_px"] <= 1
            and all(type(shell[f]) is int and shell[f] == 0 for f in COUNT_FIELDS),
            "Shell layout defect",
        )
        for finding in findings:
            require(
                finding["route"] == route
                and finding["owner_task"] == PAGE_OWNERS[route]
                and finding["severity"] in {"minor", "major", "critical"}
                and finding["reason"].strip(),
                "Unowned page finding",
            )
        positive = {f for f in COUNT_FIELDS if report[f] > 0}
        if report["page_overflow_px"] > 1:
            positive.add("page_overflow_px")
        require(positive <= {f["metric"] for f in findings}, "Page defects missing from findings")
    else:
        require(PAGE_REGIONS[route] <= regions, "Page regions absent")
        require(report["page_overflow_px"] <= 1, "Horizontal page overflow")
        require(
            all(report[f] == 0 for f in COUNT_FIELDS) and not findings,
            "Layout defect/unresolved page findings",
        )


def final_check(kind, check, digest, snapshot, root):
    require(check["result"] == "PASS", "Final check failed")
    report = json.loads(read_hashed(check["evidence"], root))
    require(
        report["schema_version"] == 1 and report["kind"] == kind and report["result"] == "PASS",
        "Final check kind/result",
    )
    require(
        report["web_sha256"] == digest and report["snapshot_sha256"] == snapshot,
        "Stale final check",
    )
    timestamp(report["executed_at"])
    require(
        report["command"].strip() and read_hashed(report["raw_evidence"], root),
        "Final raw evidence absent",
    )
    routes = set(report["routes"])
    if kind in {"zoom200", "reduced_motion"}:
        require({"/", "/calendar"} <= routes, "Final route coverage")
    elif kind in {"keyboard", "network_boundary", "boundary_review"}:
        require(ROUTES <= routes, "Final route coverage")
    if kind in {"zoom200", "keyboard", "reduced_motion", "network_boundary"}:
        require(report["browser"].strip(), "Final browser absent")
    if kind == "zoom200":
        require(
            report["zoom"] == 2 and report["zoom_method"] == "browser",
            "Actual browser zoom required",
        )
    elif kind == "reduced_motion":
        require(report["prefers_reduced_motion"] == "reduce", "Reduced-motion check absent")
    elif kind == "keyboard":
        require(
            {"Tab", "Arrow", "Escape", "focus-return"} <= set(report["interactions"]),
            "Keyboard coverage absent",
        )
    elif kind == "network_boundary":
        require(
            type(report["disallowed_request_count"]) is int
            and report["disallowed_request_count"] == 0,
            "Network boundary failure",
        )
    elif kind in {"build", "semantic_tests"}:
        require(type(report["exit_code"]) is int and report["exit_code"] == 0, "Command failed")
    else:
        require(report["reviewer_id"].strip(), "Boundary reviewer absent")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_owned(root, path, *, artifact=False):
    """No symlink traversal; bounded files only. No core/work/raw roots accepted."""
    require(isinstance(path, str) and not path.startswith("/"), "Relative owned path required")
    parts = path.split("/")
    require(all(p not in {"", ".", ".."} for p in parts), "Noncanonical path")
    prefixes = (
        (ARTIFACTS,)
        if artifact
        else (
            ARTIFACTS,
            "apps/control-center/web/",
            "apps/control-center/published/",
        )
    )
    require(path.startswith(prefixes), "Unapproved artifact/source path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(leaf)
            require(
                stat.S_ISREG(info.st_mode) and info.st_size <= 16 * 1024 * 1024, "File type/size"
            )
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(leaf)
            require(
                (info.st_size, info.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
                and len(data) == info.st_size,
                "File changed while reading",
            )
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def web_digest(root=ROOT):
    base = root / "apps/control-center/web"
    checked = root
    for part in ("apps", "control-center", "web"):
        checked = checked / part
        require(not checked.is_symlink(), "Web root symlink")
    paths = {"package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "index.html"}
    for directory in ("src", "public"):
        require(not (base / directory).is_symlink(), "Web source root symlink")
        for file in (base / directory).rglob("*"):
            require(not file.is_symlink(), "Web source symlink")
            if file.is_file():
                paths.add(file.relative_to(base).as_posix())
    entries = []
    for relative in sorted(paths):
        path = "apps/control-center/web/" + relative
        entries.append([path, sha(read_owned(root, path))])
    return sha(json.dumps(entries, separators=(",", ":")).encode())


def read_hashed(item, root):
    data = read_owned(root, item["path"], artifact=True)
    require(sha(data) == item["sha256"], "Artifact SHA mismatch")
    return data


def capture(item, route, root, *, after=False, expected_digest=None, scope="page"):
    data = read_hashed(item, root)
    width, height = png_dimensions(data)
    require(item["route"] == route and item["capture_id"].strip(), "Capture route/id")
    viewport = tuple(item["viewport"])
    require(item["dpr"] == 1 and item["zoom"] == 1, "Expected DPR1 zoom100")
    require(item["capture"] in {"viewport", "fullpage"}, "Cropped-only capture forbidden")
    require(width == viewport[0], "Capture width mismatch")
    require(
        height == viewport[1] if item["capture"] == "viewport" else height >= viewport[1],
        "Capture height mismatch",
    )
    timestamp(item["captured_at"])
    require(item["browser"].strip(), "Capture environment absent")
    require(re.fullmatch(r"[a-f0-9]{64}", item["web_sha256"]), "Capture source digest")
    if after:
        require(item["web_sha256"] == expected_digest, "Stale capture source")
        report = json.loads(read_hashed(item["layout_report"], root))
        layout(report, item, route, scope, expected_digest)
    return (viewport, item["capture"])


def validate_receipt(path, task_id, expected_digest, root=ROOT):
    require(task_id in TASK_ROUTES and TASK_ROUTES[task_id], "Unknown visual task")
    receipt = json.loads(read_owned(root, path, artifact=True))
    require(
        receipt["schema_version"] == 1 and receipt["task_id"] == task_id,
        "Wrong receipt task/schema",
    )
    scope = "shell" if task_id == "CC02.1" else "integrated" if task_id == "CC02.8" else "page"
    require(receipt["acceptance_scope"] == scope, "Wrong acceptance scope")
    require(receipt["web_sha256"] == expected_digest, "Stale receipt source")
    require(receipt["reference_sha256"] == REFERENCE_SHA, "Wrong target")
    require(
        receipt["snapshot_sha256"]
        == sha(read_owned(root, "apps/control-center/published/observatory-v1.json")),
        "Wrong data snapshot",
    )
    require(
        receipt["builder_id"]
        and receipt["reviewer_id"]
        and receipt["builder_id"] != receipt["reviewer_id"],
        "Independent reviewer required",
    )
    require(receipt["model_binding"] in {"UNVERIFIED", "VERIFIED"}, "Model status")
    require(receipt["first_review"] and receipt["fix_summary"], "Missing critique/fix pass")
    for finding in receipt["remaining_findings"]:
        require(
            finding["severity"] == "minor" and finding["reason"] and finding["owner"],
            "Unresolved major/critical finding",
        )
    screens = receipt["screens"]
    require(
        {s["route"] for s in screens} == TASK_ROUTES[task_id]
        and len(screens) == len(TASK_ROUTES[task_id]),
        "Incomplete/duplicate route coverage",
    )
    capture_ids = set()
    for screen in screens:
        require(screen["rubric_scope"] == scope, "Rubric acceptance scope")
        for item in [screen["before"], *screen["after"]]:
            require(item["capture_id"] not in capture_ids, "Duplicate capture ID")
            capture_ids.add(item["capture_id"])
        route = screen["route"]
        require(
            capture(screen["before"], route, root) == ((1600, 1000), "viewport"),
            "Initial desktop capture required",
        )
        seen = [
            capture(c, route, root, after=True, expected_digest=expected_digest, scope=scope)
            for c in screen["after"]
        ]
        required = {(view, "viewport") for view in VIEWS} | {((1600, 1000), "fullpage")}
        require(required <= set(seen), "Incomplete viewport/fullpage matrix")
        require(
            all(c["path"] != screen["before"]["path"] for c in screen["after"]),
            "First capture overwritten/reused",
        )
        require(set(screen["rubric"]) == AXES, "Incomplete design rubric")
        scores = []
        for rating in screen["rubric"].values():
            require(
                4 <= rating["score"] <= 5 and rating["reason"].strip(),
                "Visual quality below acceptance",
            )
            scores.append(rating["score"])
        require(
            sum(scores) / len(scores) >= 4.2 and screen["remaining_weaknesses"].strip(),
            "Quality/critique incomplete",
        )
    if task_id == "CC02.8":
        checks = receipt.get("final_checks", {})
        require(set(checks) == FINAL_CHECKS, "Final browser/test checks absent")
        for kind, check in checks.items():
            final_check(kind, check, expected_digest, receipt["snapshot_sha256"], root)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest", action="store_true")
    parser.add_argument("--receipt")
    parser.add_argument("--task", choices=TASK_ROUTES)
    args = parser.parse_args()
    digest = web_digest()
    if args.receipt:
        validate_receipt(args.receipt, args.task, digest)
        print("PASS: artifact/matrix conformance; aesthetic judgment remains independent review")
    else:
        print(digest)


if __name__ == "__main__":
    main()
