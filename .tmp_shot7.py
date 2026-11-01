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
js = """(()=>{
  const f=[...document.querySelectorAll('figure.blk-fig')].find(e=>e.textContent.includes('表 13-1-1'));
  if(!f) return 'nofig';
  f.scrollIntoView({block:'center'});
  const sc=document.querySelector('.main');
  return JSON.stringify({mainTop: sc?sc.scrollTop:null, winY: window.scrollY, imgs: f.querySelectorAll('img').length});
})()"""
print(cmd('Runtime.evaluate',{'expression':js,'returnByValue':True})['result'].get('result',{}).get('value'))
time.sleep(0.8)
d=cmd('Page.captureScreenshot',{'format':'png'})['result']['data']
(TMP/'img13c.png').write_bytes(base64.b64decode(d))
ws.close(); print('ok')
