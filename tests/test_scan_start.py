"""Run with: python3 -m unittest discover -s tests -v

Requires Python Playwright and Chromium (CHROMIUM_PATH overrides /usr/bin/chromium).
All browser requests are intercepted; no Instagram account or network is used.
"""
import json
import os
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SCRIPT = Path(__file__).resolve().parents[1] / 'instadm-scraper-v2.js'
MESSAGES = ''.join(
    f'<div role="presentation"><span dir="auto">Message {i:02d}</span></div>'
    for i in range(19, -1, -1)
)
FIXTURE = '''<!doctype html><html lang="en"><head><style>
body { margin: 0; }
#chat { height: 600px; width: 500px; overflow-y: auto;
        display: flex; flex-direction: column-reverse; }
#chat > div { flex: 0 0 120px; }
</style></head><body><div id="chat">''' + MESSAGES + '</div></body></html>'


class ScanStartTest(unittest.TestCase):
    def test_scan_start_modes(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage'],
            )
            try:
                for mode, position in [('current', -600), ('current', 0),
                                       ('default', -600), ('unchecked', -600)]:
                    with self.subTest(mode=mode, position=position):
                        page = browser.new_page(viewport={'width': 1280, 'height': 600})
                        try:
                            self.check_scan(page, mode, position)
                        finally:
                            page.close()
            finally:
                browser.close()

    def check_scan(self, page, mode, position):
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', lambda route: route.fulfill(
            status=200, content_type='text/html', body=FIXTURE
        ) if route.request.is_navigation_request() else route.abort())
        page.goto('https://www.instagram.com/direct/t/test-fixture/')
        page.clock.install()
        page.evaluate(SCRIPT.read_text())
        option = page.get_by_label('Start from current chat position', exact=True)
        expect(option).not_to_be_checked()
        if mode != 'default':
            option.check()
            # Switching language must preserve the selected scan mode.
            page.locator('[data-lang="es"]').click()
            expect(page.get_by_label('Empezar desde la posición actual del chat')).to_be_checked()
            page.locator('[data-lang="en"]').click()
        if mode == 'unchecked':
            option.uncheck()
        page.evaluate('''position => {
            const chat = document.querySelector('#chat');
            chat.scrollTop = position;
            window.startPosition = chat.scrollTop;
            window.scrollWrites = [];
            const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollTop');
            Object.defineProperty(chat, 'scrollTop', {
                get() { return descriptor.get.call(this); },
                set(value) {
                    window.scrollWrites.push(value);
                    descriptor.set.call(this, value);
                }
            });
        }''', position)
        self.assertEqual(page.evaluate('window.startPosition'), position)
        page.locator('.idm-scan-btn').click()
        expect(option).to_be_disabled()
        first_write = page.evaluate('window.scrollWrites[0]')
        if mode == 'current':
            self.assertGreaterEqual(first_write, position)
        else:
            self.assertLess(first_write, position)
        for _ in range(120):
            if page.locator('.idm-status-text').inner_text() == 'Scan complete.':
                break
            page.clock.run_for(1000)
        expect(page.locator('.idm-status-text')).to_have_text('Scan complete.')
        expect(option).to_be_enabled()
        self.assertEqual(page.locator('#chat').evaluate('(chat) => chat.scrollTop'), 0)
        if mode == 'current':
            self.assertTrue(all(value >= position for value in page.evaluate('scrollWrites')))
        with page.expect_download() as download_info:
            page.locator('.idm-export-json').click()
        payload = json.loads(Path(download_info.value.path()).read_text())
        texts = [item['text'] for item in payload['timeline'] if item['type'] == 'message']
        self.assertIn('Message 19', texts)
        self.assertEqual(payload['options']['startFromCurrentPosition'], mode == 'current')
        if mode == 'current':
            self.assertNotIn('Message 00', texts)
            self.assertIn('Message 10' if position == -600 else 'Message 15', texts)
        else:
            self.assertEqual(set(texts), {f'Message {i:02d}' for i in range(20)})
        self.assertFalse(errors, errors)


if __name__ == '__main__':
    unittest.main()
