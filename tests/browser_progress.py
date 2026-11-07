"""Check live progress and estimated countdown states without dataset writes."""
import argparse
import copy
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


def seconds(text):
    match=re.search(r'(\d{2}):(\d{2}):(\d{2})',text)
    assert match,text
    h,m,s=map(int,match.groups())
    return 3600*h+60*m+s


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--browser')
    parser.add_argument('--screenshot')
    args=parser.parse_args()
    errors=[]
    with sync_playwright() as pw:
        options=dict(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-webgl'])
        if args.browser:options['executable_path']=args.browser
        browser=pw.chromium.launch(**options)
        page=browser.new_page(viewport=dict(width=1500,height=950))
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='networkidle')
        page.wait_for_function('window.explorerDiagnostics?.().scene&&!explorerDiagnostics().loading')
        data=page.evaluate('async()=>await(await fetch("/api/status")).json()')
        p=data['progress']
        assert page.locator('#download-meters progress').count()==3
        assert page.locator('#dl-ready-count').inner_text()==f"{p['ready_scenes']}/{p['total_scenes']}"
        assert abs(float(page.locator('#dl-ready-percent').inner_text().strip('%'))-p['ready_percent'])<.01
        assert abs(float(page.locator('#dl-percent').inner_text().strip('%'))-p['download_percent'])<.1
        before=page.locator('#next-trip-eta').inner_text()
        page.wait_for_timeout(1150)
        after=page.locator('#next-trip-eta').inner_text()
        assert 0<seconds(before)-seconds(after)<=2,(before,after)
        meter=page.locator('#download-meters').bounding_box();transport=page.locator('.transport').bounding_box();status=page.locator('#statusbar').bounding_box()
        assert meter['y']>=transport['y']+transport['height']-.1
        assert meter['y']+meter['height']<=status['y']+.1
        if args.screenshot:
            Path(args.screenshot).parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=args.screenshot)
        # The same view must distinguish verification from readiness, and must
        # stop the countdown when status is stale, paused or disconnected.
        mocked=browser.new_page(viewport=dict(width=1500,height=950))
        mocked.on('pageerror',lambda error:errors.append(str(error)))
        mocked.clock.install()
        state=copy.deepcopy(data)
        state['progress']['next'].update(phase='verify',percent=100,saved_bytes=p['next']['archive_bytes'],eta_seconds=20)
        def status_response(route):route.fulfill(json=state)
        mocked.route('**/api/status',status_response)
        mocked.goto(args.url,wait_until='networkidle')
        mocked.wait_for_function('document.querySelector("#next-trip-phase").textContent==="verifying"')
        assert mocked.locator('#dl-ready-count').inner_text()==f"{p['ready_scenes']}/{p['total_scenes']}"
        assert mocked.locator('#next-trip-progress').evaluate('(el)=>el.value')==100
        state['progress'].update(running=False,stale=True)
        mocked.clock.fast_forward(5100)
        mocked.wait_for_function('document.querySelector("#next-trip-eta").textContent==="stale"')
        state['progress'].update(stale=False)
        mocked.clock.fast_forward(5100)
        mocked.wait_for_function('document.querySelector("#next-trip-eta").textContent==="paused"')
        state['progress'].update(running=True,next=None,ready_scenes=p['total_scenes'],ready_percent=100,download_percent=100)
        mocked.clock.fast_forward(5100)
        mocked.wait_for_function('document.querySelector("#next-trip-eta").textContent==="all ready"')
        assert mocked.locator('#dl-ready-percent').inner_text()=='100.00%'
        # Missing status must not leave a fabricated running countdown.
        mocked.unroute('**/api/status',status_response)
        mocked.route('**/api/status',lambda route:route.fulfill(status=503,body='unavailable'))
        mocked.clock.fast_forward(5100)
        mocked.wait_for_function('document.querySelector("#next-trip-eta").textContent==="offline"')
        for width in (900,390):
            page.set_viewport_size(dict(width=width,height=950))
            assert page.locator('#download-meters').evaluate('(el)=>el.scrollWidth<=el.clientWidth'),width
            for selector in ('#next-trip-eta','#dl-percent','#dl-ready-percent'):
                assert page.locator(selector).is_visible(),(width,selector)
        assert not errors,errors
        print(json.dumps(dict(download_percent=p['download_percent'],ready_percent=p['ready_percent'],ready_scenes=p['ready_scenes'],next_scene=p['next']['id'],next_percent=p['next']['percent'],countdown_before=before,countdown_after=after,verified_states=['verifying','stale','paused','all ready','offline'],browser_errors=errors),indent=2))
        browser.close()


if __name__=='__main__':main()
