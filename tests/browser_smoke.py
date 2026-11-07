"""Optional browser checks against a running KITScenes service; no dataset writes."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--browser',help='Existing Chromium executable (otherwise use Playwright Chromium)')
    parser.add_argument('--screenshot')
    parser.add_argument('--test-handoff',action='store_true',help='Simulate a preview-to-verified response change in the browser only')
    args=parser.parse_args()
    errors=[]
    with sync_playwright() as playwright:
        options=dict(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-webgl'])
        if args.browser:options['executable_path']=args.browser
        browser=playwright.chromium.launch(**options)
        page=browser.new_page(viewport=dict(width=1500,height=950))
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='networkidle')
        page.wait_for_function('window.explorerDiagnostics?.().scene && !explorerDiagnostics().loading')
        meta=page.evaluate('async()=>await(await fetch("/api/scenes/"+explorerDiagnostics().scene)).json()')
        assert page.locator('#ego-car').is_visible()
        assert page.locator('#camera-image').evaluate('(im)=>im.complete && im.naturalWidth>0')
        page.click('#metadata-toggle')
        page.click('[data-tab=files]')
        page.wait_for_function('document.querySelectorAll("#file-list .file-row").length>0')
        assert page.locator('#file-list .file-row').count()<=100
        groups=meta['file_groups']
        group=next((g for g,n in groups.items() if n>100 and 'gnss' in g),None)
        if group:
            page.select_option('#file-group',group+'/')
            page.wait_for_function('document.querySelector("#file-page").textContent.startsWith("1–100 /")')
            first=page.locator('#file-list a').first.inner_text()
            page.click('#files-next')
            page.wait_for_function('document.querySelector("#file-page").textContent.startsWith("101–")')
            assert page.locator('#file-list a').first.inner_text()!=first
            page.fill('#file-query','0000000000')
            page.wait_for_function('document.querySelectorAll("#file-list .file-row").length===1')
        page.locator('#file-list a').first.click()
        page.wait_for_function('document.querySelector("#file-preview pre")?.textContent.length>0')
        assert page.locator('#file-list .file-row').count()<=100
        page.click('[data-tab=calibration]')
        assert 'T_to_reference' in page.locator('#detail-body').inner_text()
        page.click('#metadata-toggle')
        page.click('#next')
        page.wait_for_function('explorerDiagnostics().frame===1')
        page.click('#play')
        page.wait_for_function('explorerDiagnostics().frame>=4')
        page.click('#play')
        if args.screenshot:
            Path(args.screenshot).parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=args.screenshot)
        if args.test_handoff:
            # Intercept this browser's read requests only. The real dataset and
            # server receipts are unchanged by this simulated verification event.
            stage={'verified':False}
            sid=meta['id']
            def scene_response(route):
                data=json.loads(json.dumps(meta))
                data['receipt_version']='verified-test' if stage['verified'] else 'preview-test'
                data['receipt']['mode']='full' if stage['verified'] else 'preview'
                route.fulfill(json=data)
            def catalog_response(route):
                response=route.fetch();rows=response.json()
                for row in rows:
                    if row['id']==sid:
                        row['view_version']='verified-test' if stage['verified'] else 'preview-test'
                        row['preview']=not stage['verified']
                route.fulfill(response=response,json=rows)
            page.route('**/api/scenes/'+sid,scene_response)
            page.route('**/api/scenes',catalog_response)
            page.goto(args.url.split('?')[0]+'?scene='+sid,wait_until='networkidle')
            page.wait_for_function('explorerDiagnostics().scene && !explorerDiagnostics().loading')
            page.click('#metadata-toggle')
            assert 'SHA256 pending' in page.locator('#detail-body').inner_text()
            target=min(5,len(meta['images'])-1)
            page.locator('#scrub').evaluate('(el,n)=>{el.value=String(n);el.dispatchEvent(new Event("input",{bubbles:true}));}',target)
            page.wait_for_function('(n)=>explorerDiagnostics().frame===n && !explorerDiagnostics().loading',arg=target)
            stage['verified']=True
            page.wait_for_function('!document.querySelector("#detail-body").textContent.includes("SHA256 pending")',timeout=15000)
            assert page.evaluate('explorerDiagnostics().frame')==target
        result=page.evaluate('explorerDiagnostics()')
        assert result['wasm'] and result['webgl'] and not errors
        print(json.dumps(dict(browser_errors=errors,diagnostics=result,metadata_files=meta['metadata_file_count']),indent=2))
        browser.close()


if __name__=='__main__':main()
