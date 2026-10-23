"""Remove host account ownership and local timestamps from source archives."""
from __future__ import annotations

import argparse
import copy
import gzip
from io import BytesIO
from pathlib import Path
import tarfile


def normalize(path: Path) -> None:
    """Preserve members and contents while making their archive metadata generic."""
    output = BytesIO()
    with tarfile.open(path, 'r:gz') as original:
        with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as cleaned:
                for member in original.getmembers():
                    generic = copy.copy(member)
                    generic.uid = generic.gid = 0
                    generic.uname = generic.gname = ''
                    generic.mtime = 0
                    generic.pax_headers = {}
                    source = original.extractfile(member) if member.isfile() else None
                    try:
                        cleaned.addfile(generic, source)
                    finally:
                        if source is not None:
                            source.close()
    path.write_bytes(output.getvalue())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    archives = sorted(args.directory.glob('*.tar.gz'))
    if not archives:
        parser.error('No source archives found in the supplied directory.')
    for path in archives:
        normalize(path)
    print(f'Normalized metadata in {len(archives)} source archives.')


if __name__ == '__main__':
    main()
