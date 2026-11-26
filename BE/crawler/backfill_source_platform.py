"""Add known collection provenance to legacy runtime tables; never replace existing values.

python backfill_source_platform.py --dir /path/to/kakao --platform kakao
Use a verified single-platform dataset and hold the data producer's lock when running live.
Only runtime tables are updated. Raw crawls and recommendation artifacts are not touched.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path

TABLES = ('stores', 'reviews', 'menus', 'photos')


def backfill(path: Path, platform: str) -> dict:
    before = path.stat()
    total = added = 0
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.'+path.name+'.', delete=False) as out:
            temp = Path(out.name)
            os.fchmod(out.fileno(), stat.S_IMODE(before.st_mode))
            with path.open(encoding='utf-8') as src:
                for line in src:
                    if not line.strip():
                        out.write(line)
                        continue
                    row = json.loads(line)
                    total += 1
                    if 'source_platform' not in row:
                        row['source_platform'] = platform
                        out.write(json.dumps(row, ensure_ascii=False)+'\n')
                        added += 1
                    else:
                        out.write(line)
            out.flush()
            os.fsync(out.fileno())
        current = path.stat()
        if (current.st_ino, current.st_mtime_ns, current.st_size) != (before.st_ino, before.st_mtime_ns, before.st_size):
            raise RuntimeError('Source changed while backfilling: '+path.name)
        if added:
            os.replace(temp, path)
        return {'table': path.stem, 'rows': total, 'added': added}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True, type=Path)
    parser.add_argument('--platform', required=True, choices=('kakao', 'naver'))
    args = parser.parse_args()
    for table in TABLES:
        path = args.dir / (table+'.jsonl')
        if path.exists():
            print(json.dumps(backfill(path, args.platform)))


if __name__ == '__main__':
    main()
