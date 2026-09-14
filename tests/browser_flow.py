"""Real-browser regression, intercepted locally: no requests reach production.

Run: uv run --with playwright python tests/browser_flow.py
Set BROWSER_EXECUTABLE or install Playwright Chromium on non-macOS machines.
"""
import mimetypes
import os
import sys
import subprocess
from html.parser import HTMLParser
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
COMPANY_DOMAINS = {'nexuspointsystems': 'nexuspoint-systems', 'ironvaleresources': 'ironvale-resources',
                   'meridianadvisory': 'meridian-advisory', 'metrocouncilwa': 'metro-council-wa',
                   'southerncrossfinancial': 'southern-cross-financial', 'horizonfoundation': 'horizon-foundation'}
sys.path.insert(0, str(ROOT / 'workready-api'))
import fitz
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from workready_api import app, db, auth
from workready_api.models import AssessmentResult, FeedbackDetail


def main():
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(); self.active = False; self.source = []
        def handle_starttag(self, tag, attrs):
            if tag == 'script': self.active = not dict(attrs).get('src')
        def handle_endtag(self, tag):
            if tag == 'script': self.active = False
        def handle_data(self, data):
            if self.active: self.source.append(data)
    for filename in ('index.html', 'admin.html'):
        parser = Scripts()
        parser.feed((ROOT / 'workready-deploy/console/static' / filename).read_text())
        subprocess.run(['node', '--check'], input='\n'.join(parser.source), text=True, check=True)
    with tempfile.TemporaryDirectory() as directory:
        db.DB_PATH = Path(directory) / 'browser.db'
        auth._attempts.clear()
        os.environ['LLM_PROVIDER'] = 'stub'
        app.scheduling.BOOKING_ENABLED = False
        result = AssessmentResult(fit_score=80, feedback=FeedbackDetail(strengths=['Synthetic experience'], gaps=[], suggestions=[], tailoring='Synthetic test'), proceed_to_interview=True)
        with TestClient(app.app, raise_server_exceptions=False) as client, patch.object(app, 'assess', AsyncMock(return_value=result)), patch.object(app, 'assess_interview', AsyncMock(return_value=result)), sync_playwright() as p:
            code = db.generate_codes(1, 'browser-test')[0]
            executable = os.environ.get('BROWSER_EXECUTABLE', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            browser = p.chromium.launch(headless=True, **({'executable_path': executable} if Path(executable).exists() else {}))
            context = browser.new_context()
            errors = []
            def route_request(route):
                req = route.request
                url = urlparse(req.url)
                if url.netloc == 'workready-api.eduserver.au':
                    response = client.request(req.method, url.path + ('?' + url.query if url.query else ''), content=req.post_data_buffer, headers={k: v for k, v in req.all_headers().items() if k.lower() not in ('host', 'content-length')})
                    route.fulfill(status=response.status_code, body=response.content, headers=dict(response.headers))
                    return
                roots = {'workready.eduserver.au': ROOT / 'workready-portal', 'seekjobs.eduserver.au': ROOT / 'workready-jobs/src', 'primer.eduserver.au': ROOT / 'workready-primer'}
                roots.update({domain + '.eduserver.au': ROOT / company / 'dist' for domain, company in COMPANY_DOMAINS.items()})
                if url.netloc in roots:
                    root = roots[url.netloc].resolve()
                    file = (root / (url.path.lstrip('/') or 'index.html')).resolve()
                    if file.is_relative_to(root) and file.is_file():
                        route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
                        return
                route.abort()
            context.route('**/*', route_request)
            page = context.new_page()
            page.on('dialog', lambda dialog: dialog.accept())
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('https://workready.eduserver.au/')
            expect(page.locator('#journey-steps li')).to_have_count(6)
            page.locator('#code').fill('wrong-code')
            page.locator('#signin-form button').click()
            expect(page.locator('#app-error-overlay')).to_be_visible()
            assert page.locator('#signin').evaluate('(el) => el.inert')
            page.keyboard.press('Tab')
            expect(page.locator('#app-error-ok')).to_be_focused()
            page.locator('#app-error-ok').click()
            page.locator('#code').fill(code)
            page.locator('#signin-form button').click()
            expect(page.locator('#persona-modal')).to_be_visible()
            page.locator('#persona-name').fill('Synthetic Candidate')
            page.locator('#persona-form button[type=submit]').click()
            expect(page.locator('#user-name')).to_have_text('Synthetic Candidate')
            assert page.evaluate("localStorage.getItem('workready_code')") is None

            jobs = context.new_page()
            jobs.on('pageerror', lambda error: errors.append(str(error)))
            jobs.goto('https://seekjobs.eduserver.au/')
            jobs.locator('#signin-btn').click()
            jobs.locator('#signin-code').fill(code)
            jobs.locator('#signin-form button').click()
            posting = next(p for p in db.get_all_postings() if p['company_slug'] == 'nexuspoint-systems' and p['job_slug'] == 'junior-security-analyst')
            jobs.locator('[data-apply="' + str(posting['id']) + '"]').click()
            jobs.locator('#apply-name').fill('Synthetic Candidate')
            jobs.locator('#apply-cover').fill('Synthetic cover letter with skills and experience for this test role.')
            with fitz.open() as pdf:
                pdf.new_page().insert_text((60, 60), 'Synthetic Candidate\nSkills: security Python analysis\nEmail: private@example.org')
                content = pdf.tobytes()
            jobs.locator('#apply-resume').set_input_files({'name': 'resume.pdf', 'mimeType': 'application/pdf', 'buffer': content})
            jobs.locator('#apply-form button[type=submit]').click()
            expect(jobs.locator('dialog.wr-resume-review')).to_be_visible()
            assert 'private@example.org' not in jobs.locator('dialog textarea').first.input_value()
            jobs.get_by_role('button', name='Submit this reviewed text').click()
            expect(jobs.locator('#apply-result')).to_contain_text('Application submitted')
            page.reload()
            page.locator('#dashboard-start-interview-btn').click()
            with page.expect_download() as exported:
                page.locator('a[href*="/api/v1/practice/interview/"]').first.click()
            assert exported.value.failure() is None
            page.locator('#interview-begin-btn').click()
            expect(page.locator('#interview-input')).to_be_visible()
            page.locator('#interview-input').fill('Synthetic answer about communicating clearly.')
            page.locator('#interview-send-btn').click()
            expect(page.locator('#interview-input')).to_be_enabled()
            with page.expect_response(lambda response: '/api/v1/interview/' in response.url and response.url.endswith('/end')) as ended:
                page.locator('#interview-end-btn').click()
            assert ended.value.status == 200
            page.reload()
            page.locator('#nav-tasks').click()
            expect(page.locator('.task-submit-form')).to_have_count(1)
            page.locator('.task-input').fill('Synthetic completed work with clear findings and recommendations.')
            page.locator('.task-submit-form button[type=submit]').click()
            expect(page.locator('.task-pill-passed')).to_be_visible()
            with page.expect_navigation(wait_until='load'):
                page.locator('#signout-btn').click()
            expect(page.locator('#signin')).to_be_visible()
            assert not page.evaluate('window.WRSession.active()')
            page.set_viewport_size({'width':390, 'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            for domain, company in COMPANY_DOMAINS.items():
                enrolment = db.generate_codes(1, 'browser-test')[0]
                posting = next(p for p in db.get_all_postings() if p['company_slug'] == company and p['source_type'] == 'direct')
                employer = context.new_page()
                employer.on('pageerror', lambda error: errors.append(str(error)))
                employer.goto(f'https://{domain}.eduserver.au/careers/{posting["job_slug"]}.html')
                employer.locator('[name="applicant_name"]').fill('Fictional Applicant')
                employer.locator('[name="applicant_code"]').fill(enrolment)
                employer.locator('[name="resume"]').set_input_files({'name': 'resume.pdf', 'mimeType': 'application/pdf', 'buffer': content})
                employer.locator('#apply-form button[type="submit"]').click()
                expect(employer.locator('dialog.wr-resume-review')).to_be_visible()
                employer.get_by_role('button', name='Submit this reviewed text').click()
                expect(employer.locator('#apply-result')).to_contain_text('Application submitted')
                employer.wait_for_function('!window.WRSession.active()')
                employer.close()
            assert not errors, errors
            primer = context.new_page()
            primer.on('pageerror', lambda error: errors.append(str(error)))
            primer.goto('https://primer.eduserver.au/')
            expect(primer.locator('#scene-banner')).to_have_count(1)
            expect(primer.locator('.card')).to_have_count(3)
            scenes = set()
            for _ in range(45):
                scene = primer.locator('#scene-banner').get_attribute('data-scene')
                if scene: scenes.add(scene)
                if scene == 'summary': break
                choices = primer.locator('.choice, .card')
                if not choices.count(): break
                choices.first.click()
            assert {'job-board', 'interview', 'tasks', 'lunchroom', 'exit', 'summary'} <= scenes, scenes
            assert not errors, errors
            print('Browser flow passed: bad-code dialog, persona, reviewed PDF apply, interview, task submission, logout, mobile sign-in width, all six company applications.')
            print('Primer playthrough passed:', ', '.join(sorted(scenes)))
            browser.close()


if __name__ == '__main__':
    main()
