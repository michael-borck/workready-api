"""Complete student erasure, with a durable file-cleanup queue.

Database deletion is transactional. File removals are retried if the filesystem
is temporarily unavailable; the response reports pending work rather than
claiming a complete erasure. Backup copies require the operator's expiry policy.
"""
from pathlib import Path
from workready_api import db


def checkpoint_erasure() -> bool:
    """Return True if an active reader prevents truncating the old WAL."""
    with db.get_db() as conn:
        return bool(conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0])

def cleanup_files() -> int:
    with db.get_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS erasure_files (path TEXT PRIMARY KEY)')
        rows = conn.execute('SELECT path FROM erasure_files').fetchall()
        for row in rows:
            try:
                Path(row['path']).unlink(missing_ok=True)
            except OSError:
                continue
            conn.execute('DELETE FROM erasure_files WHERE path=?', (row['path'],))
        return conn.execute('SELECT COUNT(*) FROM erasure_files').fetchone()[0]


def erase_student(student: dict, delete_identity: bool) -> dict:
    sid = student['id']
    with db.get_db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS erasure_files (path TEXT PRIMARY KEY)')
        attachments = conn.execute('''SELECT DISTINCT a.file_path FROM message_attachments a
            JOIN messages m ON m.id=a.message_id WHERE m.student_id=?''', (sid,)).fetchall()
        for row in attachments:
            conn.execute('INSERT OR IGNORE INTO erasure_files(path) VALUES (?)',
                         (str(Path(row['file_path']).absolute()),))
        aids = [r[0] for r in conn.execute('SELECT id FROM applications WHERE student_id=?', (sid,))]
        conn.execute('DELETE FROM student_sessions WHERE student_id=?', (sid,))
        conn.execute('DELETE FROM message_attachments WHERE message_id IN (SELECT id FROM messages WHERE student_id=?)', (sid,))
        conn.execute('DELETE FROM messages WHERE student_id=?', (sid,))
        for aid in aids:
            conn.execute('DELETE FROM lunchroom_posts WHERE session_id IN (SELECT id FROM lunchroom_sessions WHERE application_id=?)', (aid,))
            conn.execute('DELETE FROM lunchroom_sessions WHERE application_id=?', (aid,))
            conn.execute('DELETE FROM calendar_events WHERE application_id=?', (aid,))
            conn.execute('DELETE FROM task_submissions WHERE task_id IN (SELECT id FROM tasks WHERE application_id=?)', (aid,))
            conn.execute('DELETE FROM tasks WHERE application_id=?', (aid,))
            conn.execute('DELETE FROM interview_sessions WHERE application_id=?', (aid,))
            conn.execute('DELETE FROM interview_bookings WHERE application_id=?', (aid,))
            conn.execute('DELETE FROM stage_results WHERE application_id=?', (aid,))
        conn.execute('DELETE FROM applications WHERE student_id=?', (sid,))
        if delete_identity:
            conn.execute('DELETE FROM students WHERE id=?', (sid,))
            conn.execute('UPDATE codes SET active=0, note=NULL WHERE code=?', (student['code'],))
        else:
            conn.execute('UPDATE students SET last_login_at=NULL WHERE id=?', (sid,))
    pending = cleanup_files()
    wal_pending = checkpoint_erasure()
    return {'deleted': delete_identity, 'student_id': sid, 'applications_removed': len(aids),
            'files_pending_cleanup': pending, 'wal_checkpoint_pending': wal_pending,
            'complete': pending == 0 and not wal_pending,
            'backup_note': 'Stored backups expire according to the operator retention policy.'}
