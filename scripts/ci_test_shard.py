#!/usr/bin/env python3
"""Print the pytest files that belong to one CI shard.

Files are split by size with a greedy longest-first assignment, so shards stay
roughly balanced and every test file lands in exactly one shard.

Usage: ci_test_shard.py <tests-dir> <shard-index (1-based)> <shard-count>
"""

from __future__ import annotations

import sys
from pathlib import Path


def collect(tests_dir: Path) -> list[Path]:
    files = {*tests_dir.rglob("test_*.py"), *tests_dir.rglob("*_test.py")}
    return sorted(files)


def split(files: list[Path], count: int) -> list[list[Path]]:
    shards: list[list[Path]] = [[] for _ in range(count)]
    loads = [0] * count
    for path in sorted(files, key=lambda p: (-p.stat().st_size, str(p))):
        target = loads.index(min(loads))
        shards[target].append(path)
        loads[target] += path.stat().st_size
    return [sorted(shard) for shard in shards]


def main() -> int:
    tests_dir, index, count = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    if not 1 <= index <= count:
        print(f"shard index {index} out of range 1..{count}", file=sys.stderr)
        return 2
    files = collect(tests_dir)
    shard = split(files, count)[index - 1]
    if not shard:
        print(f"shard {index}/{count} is empty for {tests_dir}", file=sys.stderr)
        return 1
    print(f"shard {index}/{count}: {len(shard)} of {len(files)} files", file=sys.stderr)
    print("\n".join(str(p) for p in shard))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
