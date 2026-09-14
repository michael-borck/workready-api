"""Offline migration of legacy mail PDFs into durable, filtered storage.

Stop API writes first. Point --legacy-root at the old API working directory,
or a preserved copy of it. The script reports missing files and does not erase
originals unless --remove-originals is explicitly supplied.
"""
import argparse
import sqlite3
from pathlib import Path
from workready_api.pdf import store_filtered_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--legacy-root', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--legacy-prefix', type=Path, help='Original container prefix when files were copied elsewhere')
    parser.add_argument('--stored-prefix', type=Path, help='Container-visible destination prefix to record in SQLite')
    parser.add_argument('--remove-originals', action='store_true')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('Database does not exist')
    legacy = args.legacy_root.resolve()
    missing, moved = 0, 0
    with sqlite3.connect(args.database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT a.*, m.student_id FROM message_attachments a JOIN messages m ON m.id=a.message_id').fetchall()
        originals = set()
        for row in rows:
            source = Path(row['file_path'])
            if source.is_absolute() and args.legacy_prefix and source.is_relative_to(args.legacy_prefix):
                source = legacy / source.relative_to(args.legacy_prefix)
            source = (legacy / source).resolve() if not source.is_absolute() else source.resolve()
            if not source.is_relative_to(legacy) or not source.is_file():
                missing += 1
                continue
            target = store_filtered_pdf(source.read_bytes(), args.destination.resolve() / str(row['student_id']))
            stored = args.stored_prefix / target.relative_to(args.destination.resolve()) if args.stored_prefix else target
            conn.execute('UPDATE message_attachments SET file_path=?,filename=?,file_size=? WHERE id=?',
                         (str(stored), 'attachment.pdf', target.stat().st_size, row['id']))
            originals.add(source)
            moved += 1
        conn.commit()
        if args.remove_originals:
            for source in originals:
                source.unlink(missing_ok=True)
    print(f'Migrated {moved} attachments. Missing or outside supplied root: {missing}.')
    if missing:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
