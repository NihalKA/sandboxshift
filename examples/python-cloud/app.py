"""Python cloud example — NumPy matrix benchmark.

This script multiplies large random matrices using NumPy to simulate a
computationally heavy task that would slow down a developer laptop.
By pinning sandbox.mode: cloud, it always runs on AWS Fargate.

-------------------------------------------------------------------------------
Running with YAML config (uses sandboxshift.yaml in this directory):

  sandboxshift run examples/python-cloud "python app.py"

Running with CLI flags only (no YAML needed):

  sandboxshift run examples/python-cloud "python app.py" \\
    --mode cloud \\
    --cpu 4.0 \\
    --memory-mb 8192 \\
    --timeout 600 \\
    --setup "pip install -r requirements.txt"

Fargate CPU/memory must be a valid combination. See README for the table.
For 4 vCPU the valid memory range is 8 GB – 30 GB.
-------------------------------------------------------------------------------
"""

import time

import numpy as np


def benchmark(size: int = 2048, runs: int = 3) -> None:
    """Multiply *size*x*size* random matrices *runs* times and report timing."""
    print(f"Matrix multiply benchmark: {size}x{size}, {runs} runs")
    times: list[float] = []
    for i in range(runs):
        a = np.random.rand(size, size).astype(np.float32)
        b = np.random.rand(size, size).astype(np.float32)
        t0 = time.perf_counter()
        _ = a @ b
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        print(f"  Run {i + 1}: {elapsed:.3f}s")

    print(f"\nAvg: {sum(times) / len(times):.3f}s")
    print(f"Min: {min(times):.3f}s")
    print(f"Max: {max(times):.3f}s")


if __name__ == "__main__":
    benchmark()
