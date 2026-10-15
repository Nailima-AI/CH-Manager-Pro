"""Responsive browser regression checks against a disposable real HTTP server.

Run with a test-only Playwright install: python tests/browser_mobile.py.
No browser dependencies are required by the application itself.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ch_web import Service, WebServer
from test_sng import sng_bytes
from playwright.sync_api import sync_playwright, expect


def run():
    with tempfile.TemporaryDirectory() as temporary:
        service=Service(Path(temporary)/'data')
        for name in ('One.sng','Two.sng'):
            (service.upload_dir/name).write_bytes(sng_bytes(metadata={'name':'A very long sample song title for small screen review','artist':'A band with a long artist name','charter':'Sample Charter','icon':'gh3'}))
            service.execute('import',dict(upload=name,install=False))
        service.execute('install',dict(ids=[service.public_state()['archived'][0]['id']]))
        token='mobile-test-token-at-least-24-characters'
        server=WebServer(('127.0.0.1',0),service,token)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        output=Path(os.environ.get('CH_UI_SCREENSHOTS','/tmp/ch-ui-screenshots'));output.mkdir(parents=True,exist_ok=True)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch()
                for width,height in [(320,640),(390,844),(760,1024),(844,390),(1280,800)]:
                    mobile=width<=760
                    context=browser.new_context(viewport=dict(width=width,height=height),is_mobile=mobile,has_touch=mobile)
                    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                    base=f'http://127.0.0.1:{server.server_port}'
                    def no_overflow():
                        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),(width,page.evaluate('document.documentElement.scrollWidth'))
                    def navigate(tab):
                        if mobile and tab in ('settings','activity','files','trash'):
                            page.locator('#open-more').click()
                            expect(page.locator('#more-dialog')).to_be_visible()
                            page.locator(f'#more-dialog [data-tab="{tab}"]').click()
                            expect(page.locator('#more-dialog')).not_to_be_visible()
                        else:
                            nav='.mobile-nav' if mobile else '.sidebar'
                            page.locator(f'{nav} [data-tab="{tab}"]').click()
                        expect(page.locator('#page-title')).to_be_focused()
                        no_overflow()
                    page.goto(base)
                    page.locator('#token').fill(token);page.get_by_role('button',name='Open library →').click()
                    expect(page.locator('#song-list .song-row')).to_have_count(1)
                    no_overflow()
                    if mobile:
                        expect(page.locator('.sidebar')).not_to_be_visible()
                        expect(page.locator('.mobile-nav')).to_be_visible()
                        assert not page.locator('#overview').evaluate('(e)=>e.open')
                        for button in page.locator('.mobile-nav button').all():
                            box=button.bounding_box();assert box['height']>=44 and box['width']>=44
                        page.screenshot(path=str(output/f'phone-library-{width}.png'))
                        page.locator('.song-extra summary').click()
                        assert page.locator('.song-extra').evaluate('(e)=>e.open')
                        # A real polling refresh must preserve expanded details.
                        with page.expect_response('**/api/state'): pass
                        assert page.locator('.song-extra').evaluate('(e)=>e.open')
                        page.locator('.song-select').click()
                        expect(page.locator('.bulk-actions')).to_be_visible()
                        toolbar=page.locator('.bulk-actions').bounding_box();nav=page.locator('.mobile-nav').bounding_box()
                        assert toolbar['y']+toolbar['height']<=nav['y']+1
                        assert toolbar['y']>=0
                        no_overflow()
                        page.screenshot(path=str(output/f'phone-selection-{width}.png'),full_page=False)
                        page.locator('.song-select').click()
                        expect(page.locator('.bulk-actions')).not_to_be_visible()
                    else:
                        expect(page.locator('.sidebar')).to_be_visible()
                        expect(page.locator('.mobile-nav')).not_to_be_visible()
                    navigate('archived');expect(page.locator('#page-title')).to_have_text('Archived songs')
                    page.go_back();expect(page.locator('#page-title')).to_have_text('Game library')
                    navigate('duplicates');expect(page.locator('#duplicate-groups .duplicate-group')).to_have_count(1)
                    if mobile:
                        page.locator('.hash-disclosure summary').first.click()
                        page.locator('.duplicate-group').first.scroll_into_view_if_needed()
                        page.screenshot(path=str(output/f'phone-duplicates-{width}.png'),full_page=False)
                    no_overflow()
                    navigate('settings');expect(page.locator('#settings-panel')).to_be_visible()
                    page.locator('[name=songs_dir]').focus();no_overflow()
                    if mobile:
                        assert float(page.locator('[name=songs_dir]').evaluate('(e)=>getComputedStyle(e).fontSize').replace('px',''))>=16
                    navigate('activity');navigate('files');navigate('trash');navigate('songs')
                    page.locator('#open-import').click();expect(page.locator('#import-dialog')).to_be_visible();no_overflow()
                    page.get_by_role('button',name='Close import',exact=True).click()
                    if mobile:
                        page.locator('#open-more').click();page.keyboard.press('Escape')
                        expect(page.locator('#open-more')).to_have_attribute('aria-expanded','false')
                        page.locator('#open-more').click();page.locator('#mobile-logout').click()
                    else: page.locator('#logout').click()
                    expect(page.locator('#login')).to_be_visible()
                    assert not errors,errors
                    print(f'PASS: {width}x{height}: login, navigation/history, all pages, dialogs, overflow and sign-out')
                    context.close()
                browser.close()
        finally:
            server.shutdown();server.server_close();service.close()


if __name__=='__main__':run()
