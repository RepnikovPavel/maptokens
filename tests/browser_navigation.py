"""Check image/overlay registration and metric ego scale in a real browser."""
import argparse
import json

from playwright.sync_api import sync_playwright


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--url',required=True)
    p.add_argument('--browser')
    args=p.parse_args()
    errors=[]
    with sync_playwright() as playwright:
        opts=dict(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-webgl'])
        if args.browser:opts['executable_path']=args.browser
        browser=playwright.chromium.launch(**opts)
        page=browser.new_page(viewport=dict(width=2000,height=1200),device_scale_factor=2)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(args.url,wait_until='networkidle')
        page.wait_for_function('window.explorerDiagnostics?.().scene&&!explorerDiagnostics().loading')
        page.evaluate('async()=>{window.navigationSegments=(await(await fetch("/api/scenes/"+explorerDiagnostics().scene)).json()).segments}')

        def registered():
            return page.evaluate('''async()=>{
              // Force one draw, then read the overlay in that animation frame.
              document.querySelector('#overlay-toggle').dispatchEvent(new Event('change'));
              return await new Promise(resolve=>requestAnimationFrame(()=>{
                const d=explorerDiagnostics(),m=d.cameraProjection,c=document.querySelector('#camera-overlay'),gl=c.getContext('webgl2');
                const a=document.querySelector('#camera-image').getBoundingClientRect(),b=c.getBoundingClientRect();
                const candidates=[];
                for(const s of navigationSegments){
                  const p=[(s[0]+s[3])/2,(s[1]+s[4])/2,(s[2]+s[5])/2,1],q=[0,0,0,0];
                  for(let r=0;r<4;r++)for(let k=0;k<4;k++)q[r]+=m[r*4+k]*p[k];
                  const x=q[0]/q[3],y=q[1]/q[3];
                  if(q[3]>.2&&Math.abs(x)<.8&&Math.abs(y)<.8)candidates.push([x,y]);
                }
                const samples=[];
                for(let i=0;i<candidates.length;i+=Math.max(1,Math.ceil(candidates.length/20))){
                  const [x,y]=candidates[i],px=Math.round((x+1)*gl.drawingBufferWidth/2),py=Math.round((y+1)*gl.drawingBufferHeight/2),buf=new Uint8Array(7*7*4);
                  gl.readPixels(px-3,py-3,7,7,gl.RGBA,gl.UNSIGNED_BYTE,buf);
                  let alpha=0;for(let j=3;j<buf.length;j+=4)alpha=Math.max(alpha,buf[j]);samples.push(alpha);
                }
                resolve({buffer:d.cameraBuffer,view:d.cameraView,viewport:[...gl.getParameter(gl.VIEWPORT)],
                         image:{x:a.x,y:a.y,w:a.width,h:a.height},overlay:{x:b.x,y:b.y,w:b.width,h:b.height},samples});
              }));
            }''')

        before=registered()
        assert before['samples'] and min(before['samples'])>0,before
        camera=page.locator('#camera-viewport').bounding_box()
        pointer=dict(x=round(camera['x']+camera['width']*.63),y=round(camera['y']+camera['height']*.43))
        anchor=[(pointer['x']-before['image']['x'])/before['image']['w'],(pointer['y']-before['image']['y'])/before['image']['h']]
        page.locator('#camera-viewport').dispatch_event('wheel',dict(deltaY=-2000,clientX=pointer['x'],clientY=pointer['y'],bubbles=True,cancelable=True))
        page.wait_for_function('explorerDiagnostics().cameraView.zoom===6')
        zoomed=registered()
        for key in ('width','height'):
            assert zoomed['buffer'][key]==before['buffer'][key],(before,zoomed)
        for got,expected,pixels in zip([(pointer['x']-zoomed['image']['x'])/zoomed['image']['w'],(pointer['y']-zoomed['image']['y'])/zoomed['image']['h']],anchor,[zoomed['image']['w'],zoomed['image']['h']]):
            # Flex layout and CSS transforms round below the physical pixel.
            assert abs(got-expected)*pixels<.25,(got,expected)
        page.mouse.move(pointer['x'],pointer['y']);page.mouse.down();page.mouse.move(pointer['x']+80,pointer['y']-55);page.mouse.up()
        panned=registered()
        assert abs(panned['image']['x']-zoomed['image']['x']-80)<.1
        assert abs(panned['image']['y']-zoomed['image']['y']+55)<.1
        page.click('#next');page.wait_for_function('explorerDiagnostics().frame===1&&!explorerDiagnostics().loading')
        stepped=registered()
        for result in (before,zoomed,panned,stepped):
            assert min(result['samples'])>0,result
            for key in ('x','y','w','h'):assert abs(result['image'][key]-result['overlay'][key])<.02,result
            assert result['viewport'][2:]==[result['buffer']['width'],result['buffer']['height']],result
        assert stepped['view']==panned['view']
        lengths=[]
        for delta in (0,-10000,10000):
            page.locator('#map-canvas').dispatch_event('wheel',dict(deltaY=delta,bubbles=True,cancelable=True))
            size=page.evaluate('''()=>new Promise(resolve=>requestAnimationFrame(()=>{
              const d=explorerDiagnostics(),car=document.querySelector('#ego-car'),h=document.querySelector('#map-canvas').clientHeight;
              resolve([parseFloat(car.style.width)*2*d.scale/h,parseFloat(car.style.height)*2*d.scale/h]);
            }))''')
            assert abs(size[0]-2.35)<1e-5 and abs(size[1]-4.7)<1e-5,size
            lengths.append(size)
        assert not errors,errors
        print(json.dumps(dict(camera_buffer=stepped['buffer'],camera_zoom=stepped['view']['zoom'],registered_landmarks=len(stepped['samples']),ego_dimensions_m=lengths,browser_errors=errors),indent=2))
        browser.close()


if __name__=='__main__':main()
