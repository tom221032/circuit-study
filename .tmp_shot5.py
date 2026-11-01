import json, urllib.request, time, base64
from pathlib import Path
from websockets.sync.client import connect
PORT = 9333
TMP = Path(r"C:\Users\TomTomas\AppData\Local\Temp")
v = json.load(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=5))
page = [t for t in v if t.get('type') == 'page'][0]
ws = connect(page['webSocketDebuggerUrl'], max_size=200*1024*1024); mid=[0]
def cmd(m, p=None):
    mid[0]+=1; ws.send(json.dumps({'id':mid[0],'method':m,'params':p or {}}))
    while True:
        r=json.loads(ws.recv())
        if r.get('id')==mid[0]: return r
def ev(e):
    return cmd('Runtime.evaluate',{'expression':e,'returnByValue':True})['result'].get('result',{}).get('value')
def shot(name):
    d=cmd('Page.captureScreenshot',{'format':'png'})['result']['data']
    (TMP / name).write_bytes(base64.b64decode(d))
cmd('Runtime.enable'); cmd('Network.enable'); cmd('Network.setCacheDisabled',{'cacheDisabled':True})
cmd('Emulation.setDeviceMetricsOverride',{'width':1400,'height':1000,'deviceScaleFactor':1,'mobile':False})
jobs = [
  ('http://127.0.0.1:8123/#/chapter/13', 'img13.png',
   "(()=>{const p=[...document.querySelectorAll('.blk-p')].find(e=>e.textContent.includes('表 13-1-1'));if(p)window.scrollTo(0,p.getBoundingClientRect().top+window.scrollY-120);return p?1:0;})()"),
  ('http://127.0.0.1:8123/#/chapter/3?tab=ex', 'img3.png',
   "(()=>{const e=document.querySelectorAll('figure.blk-fig');if(e[2])window.scrollTo(0,e[2].getBoundingClientRect().top+window.scrollY-120);return e.length;})()"),
]
for url, out, js in jobs:
    cmd('Page.navigate',{'url':url}); time.sleep(2.2)
    cmd('Page.reload',{'ignoreCache':True}); time.sleep(3.0)
    print(url, ev(js))
    time.sleep(0.7); shot(out)
ws.close()
print('ok')
