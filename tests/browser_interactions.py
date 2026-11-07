"""Exercise map/camera picking, view controls and metric aids in Chromium."""
import argparse
import json
import math
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--browser')
    parser.add_argument('--screenshot')
    args=parser.parse_args()
    errors=[]
    with sync_playwright() as pw:
        options=dict(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-webgl'])
        if args.browser: options['executable_path']=args.browser
        browser=pw.chromium.launch(**options)
        page=browser.new_page(viewport=dict(width=1500,height=950),device_scale_factor=2)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(args.url,wait_until='networkidle')
        page.wait_for_function('window.explorerDiagnostics?.().scene&&!explorerDiagnostics().loading')
        page.evaluate('''async()=>{window.testMeta=await(await fetch('/api/scenes/'+explorerDiagnostics().scene)).json();}''')
        d=page.evaluate('explorerDiagnostics()')
        assert page.locator('#follow').is_checked() and page.locator('#loop').is_checked()
        assert d['extras']==dict(ego=True,trajectory=False,fov=False,grid=False,rings=True),d
        assert page.locator('#range-labels span').count()>=2
        page.click('#layers-toggle')
        for view in ('camera','map'):
            for category in ('hd','paint'):
                assert page.locator(f'#layer-controls [data-category={category}][data-view={view}]').is_checked()
            for category in ('sign','light','pole','other'):
                assert not page.locator(f'#layer-controls [data-category={category}][data-view={view}]').is_checked()
        # Hide just camera HD lines; map geometry is unchanged.
        page.locator('#layer-controls [data-category=hd][data-view=camera]').uncheck()
        page.wait_for_function('(n)=>explorerDiagnostics().visible.camera<n',arg=d['visible']['camera'])
        assert page.evaluate('explorerDiagnostics().visible.map')==d['visible']['map']
        page.locator('#layer-controls [data-category=hd][data-view=camera]').check()
        page.locator('#layer-controls [data-category=sign][data-view=map]').check()
        page.wait_for_function('(n)=>explorerDiagnostics().visible.map>n',arg=d['visible']['map'])
        assert page.evaluate('explorerDiagnostics().visible.camera')==d['visible']['camera']
        page.locator('#layer-controls [data-category=sign][data-view=map]').uncheck()
        page.click('#layers-close')

        def geometry():
            return page.evaluate('''()=>Object.fromEntries(['#camera-viewport','#map-canvas','#ego-car','.transport'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,[r.x,r.y,r.width,r.height]];}))''')

        before=geometry()
        # Choose an actual road-line segment with source vertices in the image.
        candidates=page.evaluate('''()=>{
          const d=explorerDiagnostics(),m=d.cameraProjection,c=document.querySelector('#camera-overlay'),r=c.getBoundingClientRect(),out=[];
          const project=p=>{const q=[0,0,0,0];for(let i=0;i<4;i++)for(let k=0;k<4;k++)q[i]+=m[i*4+k]*(p[k]??1);return q;};
          for(const s of testMeta.segments){
            if(!/^(line_thin|line_thick|stop_line|curbstone|road_border)(:|$)/.test(testMeta.groups[s[6]]))continue;
            const a=project(s.slice(0,3)),b=project(s.slice(3,6));
            if(a[3]<.3||b[3]<.3)continue;
            const points=[a,b].map(p=>[(p[0]/p[3]+1)*r.width/2,(1-p[1]/p[3])*r.height/2]);
            if(points.some(p=>p[0]<30||p[0]>r.width-30||p[1]<100||p[1]>r.height-50))continue;
            if(Math.hypot(points[0][0]-points[1][0],points[0][1]-points[1][1])<15)continue;
            const mid=[(points[0][0]+points[1][0])/2,(points[0][1]+points[1][1])/2];
            const map=document.querySelector('#map-canvas').getBoundingClientRect(),x=(s[0]+s[3])/2,y=(s[1]+s[4])/2;
            const mx=map.x+map.width/2+(x-d.center[0])*map.height/(2*d.scale),my=map.y+map.height/2-(y-d.center[1])*map.height/(2*d.scale);
            if(mx<map.x+20||mx>map.right-20||my<map.y+95||my>map.bottom-100)continue;
            out.push({index:s[7],camera:[r.x+mid[0],r.y+mid[1]],map:[mx,my]});
          }return out;
        }''')
        assert candidates,'No pickable source geometry in this scene'
        candidate=candidates[0]
        page.mouse.click(*candidate['map'])
        page.wait_for_function('explorerDiagnostics().selectedFeature!==null')
        selected_map=page.evaluate('explorerDiagnostics().selectedFeature')
        page.wait_for_function('explorerDiagnostics().selectionRender.map.points>0&&explorerDiagnostics().selectionRender.camera.points>0')
        assert geometry()==before,(before,geometry())
        assert page.locator('#inspector').is_hidden()
        page.click('#feature-close')
        page.mouse.click(*candidate['camera'])
        page.wait_for_function('explorerDiagnostics().selectedFeature!==null')
        assert geometry()==before
        selected_camera=page.evaluate('explorerDiagnostics().selectedFeature')
        assert selected_camera['index']==candidate['index'],(selected_camera,candidate)

        # Read actual WebGL highlight pixels around original selected vertices.
        highlight=page.evaluate('''()=>{
          document.querySelector('#overlay-toggle').dispatchEvent(new Event('change'));
          document.querySelector('#map-canvas').dispatchEvent(new WheelEvent('wheel',{deltaY:0}));
          return new Promise(resolve=>requestAnimationFrame(()=>{
            const d=explorerDiagnostics(),index=d.selectedFeature.index,out={};
            for(const view of ['camera','map']){
              const canvas=document.querySelector(view==='camera'?'#camera-overlay':'#map-canvas'),gl=canvas.getContext('webgl2'),r=canvas.getBoundingClientRect();
              let points=0,lines=0;
              const project=p=>{if(view==='map')return [r.width/2+(p[0]-d.center[0])*r.height/(2*d.scale),r.height/2-(p[1]-d.center[1])*r.height/(2*d.scale)];const m=d.cameraProjection,q=[0,0,0,0];for(let i=0;i<4;i++)for(let k=0;k<4;k++)q[i]+=m[i*4+k]*(p[k]??1);return q[3]>.2?[(q[0]/q[3]+1)*r.width/2,(1-q[1]/q[3])*r.height/2]:null;};
              const yellow=p=>{if(!p||p[0]<8||p[1]<8||p[0]>r.width-8||p[1]>r.height-8)return false;const buf=new Uint8Array(17*17*4),x=Math.round(p[0]*gl.drawingBufferWidth/r.width),y=Math.round((r.height-p[1])*gl.drawingBufferHeight/r.height);gl.readPixels(x-8,y-8,17,17,gl.RGBA,gl.UNSIGNED_BYTE,buf);for(let i=0;i<buf.length;i+=4)if(buf[i]>220&&buf[i+1]>165&&buf[i+2]<100)return true;return false;};
              for(const s of testMeta.segments.filter(s=>s[7]===index)){
                points+=Number(yellow(project(s.slice(0,3))))+Number(yellow(project(s.slice(3,6))));
                lines+=Number(yellow(project([(s[0]+s[3])/2,(s[1]+s[4])/2,(s[2]+s[5])/2])));
              }out[view]={points,lines};
            }resolve(out);
          }));
        }''')
        for view in ('camera','map'): assert highlight[view]['points'] and highlight[view]['lines'],highlight
        page.click('#feature-close')

        # Ring labels lie at the advertised metric radius from the ego center.
        rings=page.evaluate('''()=>{const d=explorerDiagnostics(),c=document.querySelector('#map-canvas');return [...document.querySelectorAll('#range-labels span')].map(el=>({label:el.textContent,radius:Math.hypot(parseFloat(el.style.left)-d.posePixels.x,parseFloat(el.style.top)-d.posePixels.y)*2*d.scale/c.clientHeight}));}''')
        for ring in rings:
            value,unit=ring['label'].split()
            assert abs(ring['radius']-float(value)*(1000 if unit=='km' else 1))<.01,ring
        # Known screen displacement must measure the corresponding world distance.
        page.locator('#measure').check()
        box=page.locator('#map-canvas').bounding_box()
        a=[box['x']+box['width']*.3,box['y']+box['height']*.65]
        b=[a[0]+120,a[1]+50]
        page.mouse.click(*a);page.mouse.move(*b);page.mouse.click(*b)
        page.wait_for_function('document.querySelector("#ruler-value").textContent.endsWith(" m")')
        value=float(page.locator('#ruler-value').inner_text().split()[0])
        expected=130*2*d['scale']/box['height']
        assert abs(value-expected)<=.51,(value,expected)
        page.click('#ruler-clear');page.wait_for_function('!document.querySelector("#ruler-value").textContent')
        page.locator('#measure').uncheck()
        page.click('#layers-toggle');page.locator('#layer-controls [data-extra=rings]').uncheck();page.click('#layers-close')
        page.wait_for_function('!document.querySelector("#range-labels").children.length')
        page.click('#layers-toggle');page.locator('#layer-controls [data-extra=rings]').check();page.click('#layers-close')

        def frame(n):
            page.locator('#scrub').evaluate('(el,n)=>{el.value=String(n);el.dispatchEvent(new Event("input"));}',n)
            page.wait_for_function('(n)=>explorerDiagnostics().frame===n&&!explorerDiagnostics().loading',arg=n)

        # Following keeps the car at the same screen location during playback.
        frame(0);car0=page.evaluate('explorerDiagnostics().posePixels')
        page.click('#play');page.wait_for_function('explorerDiagnostics().frame>=5');page.click('#play')
        assert page.evaluate('explorerDiagnostics().posePixels')==car0
        # Panning while attached stores an offset and remains attached.
        x,y=box['x']+box['width']*.45,box['y']+box['height']*.4
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+50,y+30);page.mouse.up()
        page.wait_for_function('Math.abs(explorerDiagnostics().followOffset[0])>1')
        assert page.locator('#follow').is_checked()
        offset=page.evaluate('explorerDiagnostics().followOffset')
        page.click('#play');page.wait_for_function('explorerDiagnostics().frame>=10');page.click('#play')
        assert page.evaluate('explorerDiagnostics().followOffset')==offset
        page.locator('#follow').uncheck();center=page.evaluate('explorerDiagnostics().center')
        frame(len(page.evaluate('testMeta.images'))-1)
        assert page.evaluate('explorerDiagnostics().center')==center
        assert page.locator('.map-viewport').evaluate('(el)=>getComputedStyle(el).overflow')=='hidden'
        # Put the SVG outside the map: no car may appear over the camera/UI.
        page.locator('#map-canvas').dispatch_event('wheel',dict(deltaY=-10000,bubbles=True,cancelable=True))
        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+600,y);page.mouse.up()
        page.wait_for_timeout(100)
        outside=page.evaluate('''()=>{const c=document.querySelector('#ego-car'),r=c.getBoundingClientRect(),m=document.querySelector('.map-viewport').getBoundingClientRect();return {outside:r.left>m.right||r.right<m.left||r.bottom<m.top||r.top>m.bottom,visible:document.elementsFromPoint(r.x+r.width/2,r.y+r.height/2).includes(c)};}''')
        assert outside['outside'] and not outside['visible'],outside
        page.click('#reset-map')
        # End of sequence wraps without stopping; explicit loop off stops there.
        page.click('#play');page.wait_for_function('explorerDiagnostics().loopCount>=1&&explorerDiagnostics().frame<5')
        assert page.evaluate('explorerDiagnostics().playing')
        page.click('#play');page.locator('#loop').uncheck();frame(len(page.evaluate('testMeta.images'))-1)
        page.click('#play');page.wait_for_function('!explorerDiagnostics().playing')
        assert page.evaluate('explorerDiagnostics().frame')==len(page.evaluate('testMeta.images'))-1
        page.locator('#loop').check();frame(0)
        if args.screenshot:
            Path(args.screenshot).parent.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=args.screenshot)
        assert not errors,errors
        print(json.dumps(dict(selected_map=selected_map,selected_camera=selected_camera,highlight_pixels=highlight,rings=rings,ruler_m=value,expected_ruler_m=expected,loop_count=page.evaluate('explorerDiagnostics().loopCount'),browser_errors=errors),indent=2))
        browser.close()


if __name__=='__main__':main()
