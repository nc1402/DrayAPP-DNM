"""Standalone integration test: authenticates to a temporary localhost DEMO controller. No hardware commands."""
import base64, json, subprocess, sys, time, urllib.request, urllib.error, socket, pathlib
ROOT=pathlib.Path(__file__).resolve().parent
with socket.socket() as s:
    s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
base=f'http://127.0.0.1:{port}'
password='DNM-demo-test-only'
proc=subprocess.Popen([sys.executable,str(ROOT/'usp_web1.py'),'--demo','--host','127.0.0.1','--port',str(port),'--password',password,'--no-browser'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def api(path,body=None,auth=True):
    headers={'X-USP-Web':'1','Content-Type':'application/json'}
    if auth: headers['Authorization']='Basic '+base64.b64encode(('dnm:'+password).encode()).decode()
    req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=10) as resp: return json.load(resp)
try:
    for i in range(40):
        try: state=api('/api/state'); break
        except OSError: time.sleep(.15)
    else: raise RuntimeError('Controller did not start')
    assert state['demo'] and state['schema']['loaded']
    try: api('/api/state',auth=False); raise AssertionError('Missing authentication guard')
    except urllib.error.HTTPError as e: assert e.code==401
    devices=api('/api/devices/scan',{})['devices']; assert len(devices)==3
    pages=api('/api/pages'); paths=['Device.DeviceInfo.']+[s['base'] for p in pages for s in p.get('sections',[])]
    for d in devices:
        assert d['status']=='online'
        for path in paths:
            result=api('/api/get',{'device':d['id'],'paths':[path]})['results'][0]
            assert result['requested']==path and not result['error'],result
    assert api('/api/schema/search?q=SSID')['results']
    assert api('/api/device/save',{'id':'oui::DEMO000004','name':'Test agent','topic':''})['ok']
    assert len(api('/api/devices')['devices'])==4
    print(f'PASS: authentication, scan, {len(devices)*len(paths)} GET calls, schema search, add demo agent')
finally:
    proc.terminate(); proc.wait(timeout=5)
