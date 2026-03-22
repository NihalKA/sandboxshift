"""Python local example — prime sieve benchmark.

This script generates all primes up to a limit using the Sieve of Eratosthenes
and prints a short summary. It is intentionally lightweight so it runs well
in a local Podman sandbox on any developer machine.

-------------------------------------------------------------------------------
Running with YAML config (uses sandboxshift.yaml in this directory):

  sandboxshift run examples/python-local "python app.py"

Running with CLI flags only (no YAML needed):

  sandboxshift run examples/python-local "python app.py" \\
    --mode local \\
    --cpu 1.0 \\
    --memory-mb 512 \\
    --timeout 120 \\
    --allow pypi.org

With a custom setup step (e.g. if you add deps to requirements.txt):

  sandboxshift run examples/python-local "python app.py" \\
    --mode local \\
    --setup "pip install -r requirements.txt"
-------------------------------------------------------------------------------
"""

import math
import time


def sieve(limit: int) -> list[int]:
    """Return all primes up to *limit* using the Sieve of Eratosthenes."""
    is_prime = bytearray([1]) * (limit + 1)
    is_prime[0] = is_prime[1] = 0
    for i in range(2, math.isqrt(limit) + 1):
        if is_prime[i]:
            is_prime[i * i :: i] = bytearray(len(is_prime[i * i :: i]))
    return [i for i, v in enumerate(is_prime) if v]


def main() -> None:
    limit = 1_000_000
    print(f"Computing primes up to {limit:,} ...")
    t0 = time.perf_counter()
    primes = sieve(limit)
    elapsed = time.perf_counter() - t0

    print(f"Found {len(primes):,} primes in {elapsed:.3f}s")
    print(f"Largest prime: {primes[-1]:,}")
    print(f"First 10: {primes[:10]}")
    print(f"Last  10: {primes[-10:]}")


if __name__ == "__main__":
    main()
