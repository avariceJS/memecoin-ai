import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from memecoin_ai.config import RunConfig
from memecoin_ai.domain import ValidationError
from memecoin_ai.experiments import RunRecorder, summarize, verify_run
from memecoin_ai.fixtures import ScriptedFixturePolicy, synthetic_events
from memecoin_ai.replay import ReferenceReplay
from memecoin_ai.storage import write_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_end_to_end_negative_fixture_and_identical_rerun(tmp_path):
    bundle = write_bundle(tmp_path / "data", synthetic_events(), "fixture")
    config = RunConfig.load(ROOT / "configs/synthetic.toml")
    replay = ReferenceReplay(
        bundle.events,
        config.execution,
        config.risk,
        quote_asset=config.quote_asset,
        initial_cash=config.initial_cash,
    )
    result = replay.run(ScriptedFixturePolicy(config), config.decision_times_ns, config.end_ns)
    assert summarize(result, config.initial_cash)["net_liquidation_pnl_quote"] < 0
    assert len(result.records) == 2
    assert result.final_balances["FAKE_A"] == 0
    assert result == replay.run(
        ScriptedFixturePolicy(config), config.decision_times_ns, config.end_ns
    )
    for name in ("a", "b"):
        recorder = RunRecorder(ROOT, tmp_path / name, bundle.identity, asdict(config))
        recorder.complete(result, config.initial_cash)
        assert verify_run(recorder.path)["status"] == "completed"
        with pytest.raises(ValidationError):
            recorder.complete(result, config.initial_cash)
    assert (tmp_path / "a/result.json").read_bytes() == (tmp_path / "b/result.json").read_bytes()
    (tmp_path / "a/metrics.json").write_text("{}")
    with pytest.raises(ValidationError, match="checksum"):
        verify_run(tmp_path / "a")


def test_cli_fixture_run_verify_and_no_overwrite(tmp_path):
    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "memecoin_ai", *map(str, args)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    bundle = tmp_path / "bundle"
    output = tmp_path / "run"
    assert cli("fixture", "--out", bundle).returncode == 0
    assert cli("fixture", "--out", bundle).returncode == 2
    result = cli("run", bundle, "--config", ROOT / "configs/synthetic.toml", "--out", output)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["edge_established"] is False
    assert cli("verify-run", output).returncode == 0
    assert (
        cli("run", bundle, "--config", ROOT / "configs/synthetic.toml", "--out", output).returncode
        == 2
    )


def test_invalid_config_and_failed_runs_are_explicit(tmp_path):
    config = RunConfig.load(ROOT / "configs/synthetic.toml")
    with pytest.raises(ValidationError):
        replace(config, decision_times_ns=(20, 10))
    recorder = RunRecorder(ROOT, tmp_path / "failed", "dataset", asdict(config))
    recorder.fail("ValidationError")
    with pytest.raises(ValidationError, match="not completed"):
        verify_run(recorder.path)


def test_provenance_must_match_running_code_and_run_is_final(tmp_path):
    config = RunConfig.load(ROOT / "configs/synthetic.toml")
    with pytest.raises(ValidationError, match="executing source"):
        RunRecorder(tmp_path, tmp_path / "wrong", "dataset", asdict(config))
    recorder = RunRecorder(ROOT, tmp_path / "valid", "dataset", asdict(config))
    recorder.fail("TestError")
    with pytest.raises(ValidationError, match="finalized"):
        recorder.fail("AnotherError")
