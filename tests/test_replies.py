"""Offline reply-preview regression checks; uses the same tools as test_scan_start."""
import html
import json
import os
from pathlib import Path
import unittest
import zipfile

from playwright.sync_api import expect, sync_playwright

SCRIPT = Path(__file__).resolve().parents[1] / 'instadm-scraper-v2.js'
ORIGINAL = 'Original <img src=x onerror=alert(1)> & special text'

# Structure supplied from Instagram: header row, quote row with a clickable
# presentation bubble, then an article containing the actual response and actions.
# Names, text, and identifiers are replaced; volatile CSS classes are omitted.
INSTAGRAM_REPLY = '''<div><div><div role="group" tabindex="-1"><div>
<div><div><span dir="auto">You replied to Alex</span></div><div></div></div>
<div><div><div><div role="button" tabindex="0"><div role="none"><div>
<div role="presentation"><div>Original fixture message
<span><img height="16" width="16" alt="😈"></span></div></div>
</div></div></div><div></div></div></div><div></div></div>
<div><div><div aria-labelledby="fixture-answer" aria-roledescription="message"
role="article" tabindex="0"><div id="fixture-answer" role="none"><div role="none">
<div><div role="presentation"><span dir="auto"><div dir="auto">Fixture response
<span><img height="16" width="16" alt="😁"></span></div></span></div></div>
</div></div></div><div><div aria-label="Message actions" role="group">
<div role="button" aria-label="Reply to message from fixture_user">
<svg><title>Reply to message from fixture_user</title></svg></div>
</div></div></div><div></div></div></div></div></div></div>'''


def fixture(role):
    def bubble(text, extra=''):
        return f'<div role="{role}">{extra}<span dir="auto">{text}</span></div>'

    groups = [
        '<div role="group"><span dir="auto">You replied to Alex</span>'
        '<div role="button"><span dir="auto">Original alpha</span></div>'
        + bubble('Same answer') + '</div>',
        '<div role="group"><span dir="auto">You replied to Alex</span>'
        f'<div role="{role}" aria-label="Original message">'
        '<span dir="auto">Original beta</span></div>'
        + bubble('Same answer') + '</div>',
        bubble('Nested answer', '<blockquote><span dir="auto">'
               + html.escape(ORIGINAL) + '</span></blockquote>'),
        '<div role="group"><span dir="auto">Replied to you</span>'
        + bubble('Missing preview answer') + '</div>',
        bubble('You replied to Alex'),
        bubble('Ordinary neighbour'),
        '<div role="group"><div aria-label="Original message">'
        '<img alt="Photo preview" width="80" height="80"></div>'
        + bubble('Photo answer') + '</div>',
        '<div role="group"><div aria-label="Original message: Accessible quote"></div>'
        + bubble('Accessible answer') + '</div>',
        # Ambiguous groups must never attach one preview to multiple replies.
        '<div role="group"><blockquote>Ambiguous quote</blockquote>'
        + bubble('Unrelated one') + bubble('Unrelated two') + '</div>',
        INSTAGRAM_REPLY if role == 'presentation' else INSTAGRAM_REPLY.replace('role="presentation"', 'role="none"'),
    ]
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
    body { margin: 0; } #chat { height: 1300px; width: 500px; overflow-y: auto; }
    #chat > div { margin: 12px; } blockquote { margin: 5px; }
    </style></head><body><div id="chat">''' + ''.join(groups) + '</div></body></html>'


class RepliesTest(unittest.TestCase):
    def test_reply_capture_and_exports(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'],
            )
            try:
                for role in ['presentation', 'article']:
                    with self.subTest(role=role):
                        page = browser.new_page(viewport={'width': 1400, 'height': 1600})
                        try:
                            self.check_replies(page, role)
                        finally:
                            page.close()
            finally:
                browser.close()

    def check_replies(self, page, role):
        page.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html', body=fixture(role)
        ) if route.request.is_navigation_request() else route.abort())
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: dialog.accept())
        page.goto('https://www.instagram.com/direct/t/reply-fixture/')
        page.clock.install()
        page.evaluate(SCRIPT.read_text())
        page.get_by_label('Start from current chat position', exact=True).check()
        page.locator('.idm-scan-btn').click()
        for _ in range(60):
            if page.locator('.idm-status-text').inner_text() == 'Scan complete.':
                break
            page.clock.run_for(1000)
        expect(page.locator('.idm-status-text')).to_have_text('Scan complete.')
        payload = json.loads(self.download(page, 'json').read_text())
        messages = [item for item in payload['timeline'] if item['type'] == 'message']
        self.assertEqual(len(messages), 11)
        same = [item for item in messages if item['text'] == 'Same answer']
        self.assertEqual({item['replyTo']['text'] for item in same}, {'Original alpha', 'Original beta'})
        by_text = {item['text']: item for item in messages}
        self.assertEqual(by_text['Nested answer']['replyTo']['text'], ORIGINAL)
        self.assertIsNone(by_text['You replied to Alex']['replyTo'])
        self.assertIsNone(by_text['Ordinary neighbour']['replyTo'])
        self.assertIsNone(by_text['Unrelated one']['replyTo'])
        self.assertIsNone(by_text['Unrelated two']['replyTo'])
        self.assertEqual(by_text['Missing preview answer']['replyTo']['text'], '')
        self.assertFalse(by_text['Missing preview answer']['replyTo']['available'])
        self.assertEqual(by_text['Photo answer']['replyTo']['text'], 'Photo preview')
        self.assertEqual(by_text['Photo answer']['images'], [])
        self.assertEqual(by_text['Accessible answer']['replyTo']['text'], 'Accessible quote')
        self.assertIn('Fixture response 😁', by_text, list(by_text))
        self.assertEqual(by_text['Fixture response 😁']['replyTo']['text'], 'Original fixture message 😈')
        self.assertEqual(by_text['Fixture response 😁']['replyTo']['context'], 'You replied to Alex')
        expect(page.locator('.idm-reply-quote')).to_have_count(7)
        expect(page.locator('.idm-reply-quote img')).to_have_count(0)
        expect(page.locator('.idm-timeline')).to_contain_text('Original message unavailable')
        page.locator('.idm-search-input').fill('Original beta')
        expect(page.locator('.idm-message-card')).to_have_count(1)
        expect(page.locator('.idm-message-text')).to_have_text('Same answer')
        # Selecting one of two equal responses must keep its own quote attached.
        page.locator('.idm-select-item').check()
        selected = json.loads(self.download(page, 'json').read_text())['timeline']
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['replyTo']['text'], 'Original beta')
        page.locator('.idm-select-item').uncheck()
        page.locator('.idm-search-input').fill('')
        txt = self.download(page, 'txt').read_text()
        md = self.download(page, 'md').read_text()
        self.assertIn('> Reply to: You replied to Alex — Original alpha', txt)
        self.assertIn('> Reply to: You replied to Alex — Original beta', md)
        self.assertIn(ORIGINAL, txt)
        self.assertIn(r'Original \<img src=x onerror=alert(1)\> & special text', md)
        with zipfile.ZipFile(self.download(page, 'zip')) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read('conversation.txt').decode(), txt)
            self.assertIn('Original alpha', archive.read('conversation.md').decode())
            zipped = json.loads(archive.read('conversation.json'))
            self.assertEqual(zipped['timeline'], payload['timeline'])
        page.locator('[data-lang="es"]').click()
        expect(page.locator('.idm-reply-quote').first).to_contain_text('En respuesta a')
        self.assertFalse(errors, errors)

    def download(self, page, kind):
        with page.expect_download() as download_info:
            page.locator('.idm-export-' + kind).click()
        download = download_info.value
        self.assertIsNone(download.failure())
        return Path(download.path())


if __name__ == '__main__':
    unittest.main()
