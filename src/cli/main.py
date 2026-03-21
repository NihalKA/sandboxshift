    print(f"Runtime: {result.runtime_mode}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"Exit code: {result.exit_code}")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    sys.exit(result.exit_code)
