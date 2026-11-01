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
cmd('Page.navigate',{'url':'http://127.0.0.1:8123/#/chapter/13'}); time.sleep(2.2)
cmd('Page.reload',{'ignoreCache':True}); time.sleep(3.0)
print(ev("(()=>{const f=[...document.querySelectorAll('figure.blk-fig')].find(e=>e.textContent.includes('表 13-1-1'));if(f)window.scrollTo(0,f.getBoundingClientRect().top+window.scrollY-160);return f?f.querySelectorAll('img').length:0;})()"))
time.sleep(0.6); shot('img13b.png')
ws.close(); print('ok')
