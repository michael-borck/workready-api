"""Regression tests for the browser contracts and private-route boundary."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault('LLM_PROVIDER', 'stub')
from fastapi.testclient import TestClient
from workready_api import app, auth, db, admin, mail
from workready_api.models import AssessmentResult, FeedbackDetail, TaskFeedback
from workready_api.pdf import extract_text
import fitz


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'LLM_PROVIDER': 'stub', 'SITES_DIR': str(Path(__file__).resolve().parents[1] / 'jobs')})
        self.environment.start()
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / 'test.db'
        auth._attempts.clear()
        self.client = TestClient(app.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.codes = db.generate_codes(2, 'privacy-test')
        self.headers = []
        self.students = []
        for code in self.codes:
            r = self.client.post('/api/v1/auth/login', json={'code': code})
            self.assertEqual(r.status_code, 200, r.text)
            self.headers.append({'Authorization': 'Bearer ' + r.json()['token']})
            self.students.append(db.get_student_by_code(code))
        self.aid = db.create_application(self.students[0]['id'], 'nexuspoint-systems', 'junior-security-analyst', 'Test role')
        self.sessions = {}
        for kind in ('hiring', 'exit', 'performance_review'):
            sid = db.create_interview_session(self.aid, 'test-mentor', 'Synthetic Mentor', kind)
            db.append_interview_message(sid, 'user', 'PRIVATE TEST TRANSCRIPT')
            self.sessions[kind] = sid

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DB_PATH = self.old_db
        self.temp.cleanup()
        self.environment.stop()

    def test_private_read_matrix(self):
        paths = [f'/api/v1/application/{self.aid}',
                 f'/api/v1/interview/{self.sessions["hiring"]}',
                 f'/api/v1/exit/application/{self.aid}',
                 f'/api/v1/perf-review/application/{self.aid}',
                 f'/api/v1/calendar/application/{self.aid}',
                 f'/api/v1/tasks/application/{self.aid}']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(self.client.get(path, headers=self.headers[1]).status_code, 404)
                owner = self.client.get(path, headers=self.headers[0])
                self.assertEqual(owner.status_code, 200, owner.text)
                self.assertEqual(owner.headers['cache-control'], 'no-store')

    def test_every_private_route_rejects_anonymous_before_validation(self):
        import re
        for route in app.app.routes:
            path = getattr(route, 'path', '')
            if not path.startswith('/api/v1/') or path.startswith(('/api/v1/admin/', '/api/v1/jobs/', '/api/v1/auth/')) or path in ('/api/v1/postings', '/api/v1/privacy'):
                continue
            concrete = re.sub(r'\{[^}]+\}', '1', path)
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                with self.subTest(route=path, method=method):
                    self.assertEqual(self.client.request(method, concrete).status_code, 401)

    def test_json_cannot_override_path_owner(self):
        own = db.create_application(self.students[1]['id'], 'nexuspoint-systems', 'junior-security-analyst', 'Other job')
        r = self.client.request('GET', f'/api/v1/exit/application/{self.aid}', headers=self.headers[1], json={'application_id': own})
        self.assertEqual(r.status_code, 404)

    def test_completed_placement_is_read_only(self):
        db.set_application_status(self.aid, 'completed')
        sid = self.sessions['hiring']
        self.assertEqual(self.client.get(f'/api/v1/interview/{sid}', headers=self.headers[0]).status_code, 200)
        response = self.client.post('/api/v1/interview/message', headers=self.headers[0], json={'session_id': sid, 'message': 'Change my completed record'})
        self.assertEqual(response.status_code, 409)

    def test_writes_and_kind_check(self):
        mid = db.create_message(self.students[0]['id'], 'Test', 'Test', 'Private')
        path = f'/api/v1/inbox/message/{mid}/read'
        self.assertEqual(self.client.post(path).status_code, 401)
        self.assertEqual(self.client.post(path, headers=self.headers[1]).status_code, 404)
        self.assertFalse(db.get_message(mid)['is_read'])
        self.assertEqual(self.client.post(path, headers=self.headers[0]).status_code, 200)
        sid = self.sessions['exit']
        self.assertEqual(self.client.get(f'/api/v1/interview/{sid}', headers=self.headers[0]).status_code, 404)
        self.assertEqual(self.client.post('/api/v1/interview/message', headers=self.headers[1], json={'session_id': self.sessions['hiring'], 'message': 'attack'}).status_code, 404)

    def test_session_revocation_logout_and_no_code_in_state(self):
        data = self.client.get('/api/v1/me/state', headers=self.headers[0]).json()
        self.assertNotIn('code', data)
        self.assertNotIn(self.codes[0].replace('-', '').lower(), data['handle'])
        db.revoke_code(self.codes[0])
        self.assertEqual(self.client.get('/api/v1/me/progress', headers=self.headers[0]).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/logout', headers=self.headers[1]).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/me/state', headers=self.headers[1]).status_code, 401)

    def test_login_budget_checked_before_valid_code(self):
        auth._attempts.clear()
        for _ in range(10):
            self.assertEqual(self.client.post('/api/v1/auth/login', json={'code': 'WR-INVALID'}).status_code, 401)
        r = self.client.post('/api/v1/auth/login', json={'code': self.codes[0]})
        self.assertEqual(r.status_code, 429)
        self.assertIn('retry-after', r.headers)

    def test_anonymous_upload_cannot_call_assessor(self):
        mock = AsyncMock()
        with patch.object(app, 'assess', mock):
            r = self.client.post('/api/v1/resume', data={'applicant_code': self.codes[0]}, files={'resume': ('x.pdf', b'%PDF-fake', 'application/pdf')})
        self.assertEqual(r.status_code, 401)
        mock.assert_not_awaited()
        self.assertEqual(self.client.post('/api/v1/resume', content=b'x', headers={'Content-Length': str(auth.MAX_REQUEST_BYTES + 1)}).status_code, 413)

    def test_lunchroom_payload_does_not_crash(self):
        r = self.client.post('/api/v1/lunchroom/invitation/999/pick-slot', headers=self.headers[0], json={'scheduled_at': '2030-01-01T00:00:00Z'})
        self.assertEqual(r.status_code, 404, r.text)
        slot = '2030-01-01T12:00:00+00:00'
        sid = db.create_lunchroom_invitation(self.aid, 'routine_lunch', [{'slug': 'test', 'name': 'Synthetic colleague', 'role': 'Mentor'}], [slot], 'manual')
        r = self.client.post(f'/api/v1/lunchroom/invitation/{sid}/pick-slot', headers=self.headers[1], json={'scheduled_at': slot})
        self.assertEqual(r.status_code, 404)
        r = self.client.post(f'/api/v1/lunchroom/invitation/{sid}/pick-slot', headers=self.headers[0], json={'scheduled_at': slot})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'accepted')

    def test_browser_quick_apply_and_contact_filter(self):
        with fitz.open() as pdf:
            pdf.new_page().insert_text((60, 60), 'Skills: Python security\nName: Synthetic Identifier\nEmail: private@example.org')
            content = pdf.tobytes()
        result = AssessmentResult(fit_score=80, feedback=FeedbackDetail(strengths=['Skills'], gaps=[], suggestions=[], tailoring='Good'), proceed_to_interview=True)
        assessor = AsyncMock(return_value=result)
        posting = db.get_all_postings()[0]
        with patch.object(app, 'assess', assessor):
            r = self.client.post('/api/v1/resume', headers=self.headers[1], data={'posting_id': posting['id'], 'job_title': '', 'cover_letter': 'Contact me at private@example.org'}, files={'resume': ('private-name.pdf', content, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn('private@example.org', str(assessor.call_args))
        self.assertNotIn('Synthetic Identifier', str(assessor.call_args))
        self.assertEqual(assessor.call_args.kwargs['job_title'], posting['listing_title'])

    def test_complete_erasure_with_attachments_tasks_and_sessions(self):
        mid = db.create_message(self.students[0]['id'], 'Test', 'Test', 'Body', application_id=self.aid)
        original = Path(self.temp.name) / 'test.pdf'
        original.write_bytes(b'SYNTHETIC')
        db.create_attachment(mid, 'test.pdf', str(original), 9)
        with db.get_db() as conn:
            cursor = conn.execute("INSERT INTO tasks (application_id,title,brief,description,difficulty,sequence,assigned_at) VALUES (?, 'Test', 'Test', 'Test', 'hard', 1, ?)", (self.aid, db._now()))
            conn.execute("INSERT INTO task_submissions (task_id,body,created_at) VALUES (?, 'Synthetic', ?)", (cursor.lastrowid, db._now()))
        db.create_calendar_event(self.aid, 'task_deadline', 'Test', db._now())
        with patch.object(admin, 'ADMIN_TOKEN', 'test-admin'):
            r = self.client.delete('/api/v1/admin/students/' + str(self.students[0]['id']), headers={'Authorization': 'Bearer test-admin'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['complete'])
        self.assertFalse(original.exists())
        self.assertIsNone(db.get_student_by_id(self.students[0]['id']))
        self.assertFalse(db.redeem_code(self.codes[0]))
        self.assertEqual(self.client.get('/api/v1/me/state', headers=self.headers[0]).status_code, 401)

    def test_final_task_resubmit_stays_on_placement(self):
        db.advance_stage(self.aid, 'placement')
        with db.get_db() as conn:
            cursor = conn.execute("INSERT INTO tasks (application_id,title,brief,description,difficulty,sequence,status,assigned_at,visible_at) VALUES (?, 'Task', 'Brief', 'Detail', 'hard', 1, 'assigned', '2020-01-01', '2020-01-01')", (self.aid,))
            tid = cursor.lastrowid
        reviewer = AsyncMock(return_value=(50, 'resubmit', TaskFeedback(strengths=[], improvements=['Revise'], summary='Please revise')))
        with patch.object(app, 'review_task_submission', reviewer), patch.object(app.scheduling, 'LUNCHROOM_TRIGGER', 'disabled'):
            r = self.client.post(f'/api/v1/tasks/{tid}/submit', headers=self.headers[0], data={'body': 'My work'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(db.get_application(self.aid)['current_stage'], 'placement')

    def test_filtered_mail_pdf_has_unique_storage_and_no_original_name(self):
        from workready_api.pdf import store_filtered_pdf
        with fitz.open() as pdf:
            pdf.new_page().insert_text((60, 60), 'Email: private@example.org\nSkills: Python')
            content = pdf.tobytes()
        directory = Path(self.temp.name) / 'attachments'
        first = store_filtered_pdf(content, directory)
        second = store_filtered_pdf(content, directory)
        self.assertNotEqual(first, second)
        self.assertNotIn('private@example.org', extract_text(first.read_bytes()))


if __name__ == '__main__':
    unittest.main()
