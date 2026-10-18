"""Bounded actual-device forward/backward timings; no extrapolated CUDA results."""

from __future__ import annotations

import os
import platform
import resource
import statistics
import time
from pathlib import Path

import torch

from memecoin_ml.data import Row, seal, write
from memecoin_ml.models import INPUTS, KINDS, Brain, ModelConfig
from memecoin_ml.training import device_for, source_identity


def benchmark(output: Path, *, iterations: int = 10) -> Row:
    if not 3 <= iterations <= 20:
        raise ValueError("bounded benchmark needs 3..20 iterations")
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise ValueError("MPS CPU fallback must be disabled")
    output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(False)
    results = []
    for device_name in ("cpu", "mps"):
        if device_name == "mps" and not torch.backends.mps.is_available():
            results.append(dict(device=device_name, status="unavailable"))
            continue
        device = device_for(device_name)
        for kind in KINDS:
            failed = False
            for width, context in ((16, 8), (64, 32)):
                if failed:
                    break
                for batch_size in (1, 32):
                    config = ModelConfig(kind, width, context)
                    entry: Row = dict(
                        device=device_name, model=config.as_dict(), batch_size=batch_size
                    )
                    try:
                        torch.manual_seed(7)
                        model = Brain(config).to(device)
                        x = torch.randn(batch_size, context, INPUTS, device=device)
                        lengths = torch.full(
                            (batch_size,), context, device=device, dtype=torch.long
                        )
                        actions = torch.tensor([[0, 0.001, 0, 20]], device=device).expand(
                            batch_size, -1
                        )
                        timings: dict[str, list[float]] = {
                            "inference_ms": [],
                            "forward_backward_ms": [],
                        }
                        for phase in timings:
                            for iteration in range(iterations + 3):
                                if device_name == "mps":
                                    torch.mps.synchronize()
                                start = time.perf_counter_ns()
                                if phase == "inference_ms":
                                    model.eval()
                                    with torch.no_grad():
                                        q, logits = model(x, lengths, actions)
                                else:
                                    model.train()
                                    model.zero_grad(set_to_none=True)
                                    q, logits = model(x, lengths, actions)
                                    (q.square().mean() + logits.square().mean()).backward()
                                if device_name == "mps":
                                    torch.mps.synchronize()
                                elapsed = (time.perf_counter_ns() - start) / 1e6
                                if not torch.isfinite(q).all() or not torch.isfinite(logits).all():
                                    raise ValueError("nonfinite benchmark")
                                if iteration >= 3:
                                    timings[phase].append(elapsed)
                        entry.update(
                            status="ok",
                            parameters=sum(p.numel() for p in model.parameters()),
                            timings={
                                key: dict(
                                    median=statistics.median(values),
                                    max=max(values),
                                    samples=values,
                                )
                                for key, values in timings.items()
                            },
                            mps_current_bytes=torch.mps.current_allocated_memory()
                            if device_name == "mps"
                            else None,
                        )
                    except (RuntimeError, ValueError, NotImplementedError) as exc:
                        # Stop this architecture/device after its first failure; no retries/tuning.
                        entry.update(
                            status="failed",
                            category="device_operation_failure",
                            exception=type(exc).__name__,
                            detail=str(exc)[:500],
                        )
                        failed = True
                    results.append(entry)
                    if failed:
                        break
    report = dict(
        qualification="engineering_only",
        iterations=iterations,
        warmup=3,
        source=source_identity(),
        torch=str(torch.__version__),
        machine=platform.machine(),
        platform=platform.platform(),
        threads=1,
        process_peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        rss_units="bytes on macOS, KiB on Linux; entire process high-water mark",
        results=results,
        interpretation="synchronous kernels; no pipeline or market claims",
    )
    write(output / "report.json", report)
    seal(output)
    return report
