"""Explicit sparse candle selection checks against immutable synthetic descriptors."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from memecoin_experience import observations, sparse


def _source(profile):
    legacy = sparse._LEGACY_FEATURES
    morphology = sparse._MORPHOLOGY_FEATURES
    context = sparse._CONTEXT_FEATURES
    implementation = "a" * 64
    numerics = "b" * 64
    cutoff = 123456789
    source_pin = "c" * 64
    base_process = "qualified_provider_bar_closes; missing bars do not certify inactivity"
    features = legacy
    producer = dict(
        producer="continuous-observation-adapter-v1",
        implementation=implementation,
        numerics=numerics,
    )
    if profile == "candle-morphology-v1":
        features = legacy + morphology
        producer = dict(
            producer="continuous-observation-adapter-v2",
            implementation=implementation,
            numerics=numerics,
            profile=dict(
                name=profile,
                version=1,
                features=morphology,
                rules=sparse._MORPHOLOGY_RULES,
            ),
        )
        base_process += "; candle-morphology-v1: " + sparse._MORPHOLOGY_RULES
    elif profile == "candle-context-v1":
        features = legacy + morphology + context
        producer = dict(
            producer="continuous-observation-adapter-v3",
            implementation=implementation,
            numerics=numerics,
            profile=dict(
                name=profile,
                version=1,
                features=features,
                parameters=sparse._CONTEXT_PARAMETERS,
                revision_selection="geckoterminal/usd/base/5min/first-qualified-receipt",
                formulas=dict(
                    prior_close_log_return=(
                        "log(current.close / immediately_preceding_retained.close) only "
                        "when immediately_preceding_retained.open == current.open-300s"
                    ),
                    recent_close_position=(
                        "(current.close-min(contributor.low))/(max(contributor.high)-"
                        "min(contributor.low)) when range positive"
                    ),
                    context_count="retained observed contributor count including current",
                    context_span_s="latest contributor open minus earliest contributor open",
                    context_max_gap_s=(
                        "maximum consecutive contributor open-time difference; missing "
                        "for singleton"
                    ),
                    context_contiguous=(
                        "one iff count>=2 and all consecutive gaps equal 300s; missing "
                        "for singleton"
                    ),
                    context_ready="one iff count=12 and context_contiguous=1, otherwise zero",
                    context_zero_range=(
                        "one iff count>=2 and max(high)==min(low); missing below count two"
                    ),
                ),
                morphology_rules=sparse._MORPHOLOGY_RULES,
                rules=sparse._CONTEXT_RULES,
                identity=dict(
                    source_pin=source_pin,
                    cutoff_ns=cutoff,
                    implementation_sha256=implementation,
                    numerics_sha256=numerics,
                ),
            ),
        )
        base_process += "; candle-morphology-v1: " + sparse._MORPHOLOGY_RULES
        base_process += "; candle-context-v1: " + sparse._CONTEXT_RULES
    contract = dict(
        version="market-observations-v1",
        role="train",
        availability="historical_replay",
        streams={"qualified_price_bar": dict(process=base_process, features=features)},
    )
    return SimpleNamespace(
        pin="d" * 64,
        schema=contract,
        manifest=dict(
            binding=dict(
                contract=contract,
                cutoff_ns=cutoff,
                sources=[dict(kind="continuous", pin=source_pin), producer],
            )
        ),
    )


def _plan():
    return dict(stream="qualified_price_bar", relative_features=[])


@pytest.mark.parametrize("profile", ["legacy-v1", "candle-morphology-v1", "candle-context-v1"])
def test_explicit_profile_selection_persists_full_authenticated_descriptor(profile):
    source = _source(profile)
    selected = sparse._observation_profile(source, profile, _plan())
    assert selected["name"] == profile
    assert selected["source_pin"] == "d" * 64
    assert selected["continuous_source_pin"] == "c" * 64
    assert selected["stream"] == "qualified_price_bar"
    assert selected["features"] == source.schema["streams"]["qualified_price_bar"]["features"]
    assert selected["availability"] == "historical_replay"
    assert selected["cutoff_ns"] == 123456789
    assert selected["producer"] == source.manifest["binding"]["sources"][1]


@pytest.mark.parametrize("profile", ["candle-morphology-v1", "candle-context-v1"])
@pytest.mark.parametrize("mutation", ["feature", "parameter", "identity", "stream"])
def test_explicit_selection_rejects_descriptor_impostors(profile, mutation):
    source = _source(profile)
    source = SimpleNamespace(schema=deepcopy(source.schema), manifest=deepcopy(source.manifest))
    producer = source.manifest["binding"]["sources"][1]
    if mutation == "feature":
        producer["profile"]["features"][0]["unit"] = "wrong-unit"
    elif mutation == "parameter":
        producer["profile"]["parameters"] = dict(sparse._CONTEXT_PARAMETERS, max_count=11)
    elif mutation == "identity":
        producer["profile"]["identity"] = dict(
            source_pin="c" * 64,
            cutoff_ns=123456789,
            implementation_sha256="a" * 64,
            numerics_sha256="b" * 64,
        )
        producer["profile"]["identity"]["source_pin"] = "d" * 64
    else:
        source.schema["streams"]["qualified_price_bar"]["features"][0]["unit"] = "wrong-unit"
        source.manifest["binding"]["contract"] = source.schema
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, profile, _plan())


def test_explicit_profiles_reject_relative_features_and_unknown_names():
    source = _source("legacy-v1")
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, "legacy-v1", dict(relative_features=["close_usd"]))
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, "impostor-v1", _plan())
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, ["legacy-v1"], _plan())
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, "legacy-v1", dict(stream="ticks", relative_features=[]))


def test_legacy_contract_requires_exact_v1_producer_and_stream():
    source = _source("legacy-v1")
    source.manifest["binding"]["sources"][1]["producer"] = "continuous-observation-adapter-v99"
    with pytest.raises(ValueError, match="observation_profile"):
        sparse._observation_profile(source, "legacy-v1", _plan())


@pytest.mark.parametrize("profile", [None, "legacy-v1"])
def test_materialize_keeps_legacy_selection_shape_or_persists_explicit_profile(
    monkeypatch, tmp_path, profile
):
    source = _source("legacy-v1")
    source.manifest["records"] = 0
    source.index = lambda _: SimpleNamespace(close=lambda: None)
    source.pin = "d" * 64
    monkeypatch.setattr(observations, "Source", lambda *args, **kwargs: source)
    monkeypatch.setattr(sparse, "samples", lambda *args, **kwargs: iter(()))
    captured = {}

    def build(*args, **kwargs):
        captured.update(kwargs)
        return {"snapshot": "e" * 64}

    monkeypatch.setattr(sparse, "build", build)
    plan = dict(
        stream="qualified_price_bar",
        context=1,
        max_history_ns=10,
        targets=[dict(name="count", kind="observed_process_count", horizon_ns=1, feature=None)],
        lifecycle=[],
        lifecycle_origins=False,
        relative_features=[],
    )
    sparse.materialize("source", source.pin, tmp_path / "output", plan=plan, profile=profile)
    selection = captured["selection"]
    assert selection["sparse_plan"] == plan
    if profile is None:
        assert selection == {"sparse_plan": plan}
    else:
        assert selection["observation_profile"]["name"] == profile
        assert selection["observation_profile"]["producer"] == source.manifest["binding"][
            "sources"
        ][1]
