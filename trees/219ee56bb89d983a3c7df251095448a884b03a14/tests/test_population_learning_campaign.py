import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest
from test_population_acquisition import plan

from memecoin_experience.storage import atomic_json, digest
from memecoin_population.acquisition import capture


def module():
    spec = importlib.util.spec_from_file_location(
        "population_learning", Path(__file__).parents[1] / "tools/population_learning.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_campaign_end_to_end_and_repeat(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    p = plan()
    p.update(created_ns=10**17, expires_ns=10**18, max_requests=4)
    p["cases"] = [
        dict(
            pool=pool,
            base_mint="M" + pool,
            quote_mint="Q",
            role=role,
            event_s=start,
            month="synthetic",
        )
        for pool, role, start in (
            ("A", "train", 0),
            ("B", "train", 0),
            ("C", "research_evaluation", 40200),
            ("D", "research_evaluation", 40200),
        )
    ]
    p["jobs"] = [
        dict(pool=c["pool"], start_s=c["event_s"], end_s=c["event_s"] + 30000) for c in p["cases"]
    ]
    p["protocol"] = dict(
        context=4,
        horizons=[1, 3, 6],
        split=dict(train_start_s=0, train_end_s=9000, validation_end_s=18000, test_end_s=30000),
        evaluation=dict(start_s=40200, end_s=70200),
        baseline_pools=["A"],
        quality=dict(
            name="causal_activity_v1", min_positive_volume_fraction=0.25, min_context_volume_usd=100
        ),
        normalization="constant_feature_scale_one_v1",
        models=["narrow_fresh", "broad_fresh", "narrow_compute_control"],
        config=dict(width=4, epochs=1, batch=16, seed=20261008, checkpoint_every=2, cpu_threads=1),
        population_bias="synthetic only",
        scope="synthetic_integration",
    )
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    atomic_json(campaign / "plan.json", p)
    clock = iter(range(10**17, 10**17 + 100 * 10**9, 4 * 10**9))

    class Response(io.BytesIO):
        status = 200

    def opener(request, **kwargs):
        pool = request.full_url.split("/pools/")[1].split("/")[0]
        if pool == "D":
            raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, io.BytesIO(b"{}"))
        case = next(c for c in p["cases"] if c["pool"] == pool)
        body = dict(
            meta=dict(base=dict(address=case["base_mint"]), quote=dict(address="Q")),
            data=dict(
                attributes=dict(
                    ohlcv_list=[
                        [
                            case["event_s"] + i * 300,
                            1,
                            1.2,
                            0.8,
                            1 + (i % 3) * 0.01,
                            0 if pool == "B" and i < 12 else 100,
                        ]
                        for i in range(100)
                    ]
                )
            ),
        )
        return Response(json.dumps(body).encode())

    capture(
        campaign / "capture",
        p,
        guard=lambda: None,
        opener=opener,
        clock=lambda: next(clock),
        sleep=lambda n: None,
    )
    runner = module()
    root = tmp_path / "learning"
    result = runner.run(campaign, root)
    assert all(r["status"] == "completed" for r in result["training"].values())
    assert result["evaluation_source"]["enrollment"] == 2
    assert result["scores"]["structural"]["broad_fresh"]["supported_pools"] == 1
    assert len(result["scores"]["structural"]["paired"]["comparisons"]) == 6
    assert runner.run(campaign, root) == result
    prepared = json.loads((root / "prepared.json").read_text())
    assert (
        prepared["counts"]["broad_fresh"]["train_usable"]
        > prepared["counts"]["narrow_fresh"]["train_usable"]
    )
    assert (
        prepared["counts"]["broad_fresh"]["test_usable"]
        == prepared["counts"]["narrow_fresh"]["test_usable"]
    )
    # Reconciliation independently checks the terminal ledger, context funnel and actual draws.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "tools"))
    from population_campaign_report import report

    receipt = report(campaign, root)
    assert receipt["acquisition"]["requests"] == 4
    assert receipt["acquisition"]["outcomes"] == {"received": 3, "unsupported": 1}
    assert receipt["sources"]["research_evaluation"]["funnel"]["per_pool"]["D"] == {}
    assert receipt["candidates"]["broad_fresh"]["rows_seen"] > 0
    assert receipt == report(campaign, root)
    original = json.loads((root / "result.json").read_text())
    changed = json.loads(json.dumps(original))
    changed["scores"]["structural"]["paired"]["comparisons"][0]["micro_difference"] += 1
    (root / "result.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="population_report_comparison"):
        report(campaign, root)
    (root / "result.json").write_text(json.dumps(original))
    declaration = dict(
        version="population-learning-plan-v1",
        acquisition=digest(p),
        protocol=json.loads(json.dumps(p["protocol"])),
        reason="Synthetic split revision",
        evidence=["a" * 64],
    )
    declaration["protocol"]["split"].update(train_end_s=12000, validation_end_s=21000)
    revised = runner.run(campaign, tmp_path / "revised", declaration=declaration)
    assert revised["binding"]["learning_plan"] == digest(declaration)
    assert revised["recipe_fixed_before_acquisition"] is False
    assert report(campaign, tmp_path / "revised")["protocol"] == declaration["protocol"]
    assert json.loads((campaign / "plan.json").read_text()) == p
    with pytest.raises(ValueError, match="experience_immutable_conflict"):
        runner.run(campaign, root, declaration=declaration)
    assert not (root / "learning-plan.json").exists()
    invalid = dict(declaration, acquisition="b" * 64)
    with pytest.raises(ValueError, match="population_learning_declaration"):
        runner.run(campaign, tmp_path / "invalid", declaration=invalid)
    declaration["protocol"]["split"].update(
        train_end_s=9000, validation_end_s=9001, test_end_s=9002
    )
    failed = runner.run(campaign, tmp_path / "unsupported", declaration=declaration)
    assert failed["evaluation"] == "not_opened_no_candidates"
    assert not (tmp_path / "unsupported/research_evaluation-source").exists()
    assert not (tmp_path / "unsupported/generations").exists()
    assert (
        report(campaign, tmp_path / "unsupported")["evaluation_state"] == "not_opened_no_candidates"
    )
    declaration["protocol"]["context"] = 256
    empty = runner.run(campaign, tmp_path / "empty", declaration=declaration)
    assert empty["evaluation"] == "not_opened_no_candidates"
    assert report(campaign, tmp_path / "empty")["raw_snapshot"] is None
    # A registered all-structural score cannot silently receive an admitted view.
    from memecoin_experience import evaluation

    registered = json.loads((root / "registered.json").read_text())
    outcome = result["training"]["broad_fresh"]
    with pytest.raises(ValueError, match="admission_protocol"):
        evaluation.score(
            root / "protocols",
            registered["broad_fresh"]["structural"],
            root / "store",
            result["evaluation_admission"]["snapshot"],
            Path(outcome["job"]) / "training/artifact",
            root / "bad-evaluation",
        )
