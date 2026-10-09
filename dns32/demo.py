"""Local browser demo. Each button starts a fresh, isolated real DNS computation."""
import argparse,hmac,json,os,subprocess,threading,time,uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
ROOT=Path(__file__).resolve().parent.parent
lock=threading.Lock();job=None
ACCESS_TOKEN=os.environ.get('DNSDEMO_ACCESS_TOKEN','')
SERVICE_ID=os.environ.get('DNSDEMO_SERVICE_ID','dnsdoom-demo')
IMAGE=os.environ.get('DNSDEMO_IMAGE','dns32:local')

def save_job(item):
    data={k:v for k,v in item.items() if k not in ('process','output','thread')}
    path=item['output']/'job.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.replace(path)

def restore_job(directory):
    data=read(directory/'job.json')
    if not data:return None
    data['output']=directory
    if data['phase'] in ('starting','running'):
        data.update(phase='failed',finished=time.time(),error='Service restarted during this run. Start a new computation.')
        save_job(data)
    return data

def read(path):
    try:return json.loads(path.read_text())
    except (OSError,json.JSONDecodeError):return None

def status():
    with lock:
        if job is None:return {'phase':'idle'}
        data={k:v for k,v in job.items() if k not in ('process','output','thread')}
        out=job['output']
        report=read(out/'run.json');progress=read(out/'progress.json');failure=read(out/'failure.json')
        if report:report={k:v for k,v in report.items() if k not in ('sample_dns_requests','milestones','registers','instruction_counts','messages')}
        data.update(report=report,progress=progress,failure=failure,preview=(out/'preview.png').exists())
        if data['phase']=='starting' and progress:data['phase']='running'
        if data['phase']=='failed':
            log=out/'console.log'
            if log.exists() and not data.get('error'):data['error']=log.read_text(errors='replace')[-3000:]
        return data

def work(item,command):
    try:
        run_job(item,command)
    except Exception as error:
        with lock:
            item.update(phase='failed',finished=time.time(),error=str(error))
            save_job(item)

def run_job(item,command):
    out=item['output']
    with (out/'console.log').open('w') as f:
        with lock:
            if item['phase']=='stopped':return
            process=subprocess.Popen(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
            item['process']=process
        while process.poll() is None:
            with lock:cancelled=item['phase']=='stopped'
            if cancelled:
                # Cancellation may arrive before Docker finishes creating the
                # container. Retry until the CLI exits; don't leave a job behind.
                subprocess.run(['docker','rm','-f',SERVICE_ID+'-'+item['id']],capture_output=True)
            time.sleep(.1)
        code=process.returncode
    with lock:
        if item['phase']!='stopped':item['phase']='complete' if code==0 else 'failed'
        item['exit_code']=code;item['finished']=time.time()
        save_job(item)

def start(program,backend,fast):
    global job
    with lock:
        if job and (job['phase'] in ('starting','running') or job.get('thread') and job['thread'].is_alive()):raise ValueError('A computation is already running or stopping')
        # Bound accumulated run artifacts; historical evidence without job.json is retained.
        previous=sorted((ROOT/'artifacts/live').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        import shutil
        for old in previous[49:]:shutil.rmtree(old.parent)
        ident=uuid.uuid4().hex[:10];relative=Path('artifacts/live')/ident;out=ROOT/relative;out.mkdir(parents=True)
        command=['docker','run','--rm','--name',SERVICE_ID+'-'+ident,'--label','dnsdoom.service='+SERVICE_ID,
                 '--network','none','--shm-size','4g','--memory','6g','--cpus','2','--pids-limit','256',
                 '--user',f'{os.getuid()}:{os.getgid()}','--mount',f'type=bind,src={ROOT},dst=/work',IMAGE]
        if program=='doom':
            command+=['artifacts/tick-checkpoint/program.elf','--checkpoint','artifacts/tick-checkpoint','--steps','3000000','--seconds','1800','--preview','5']
        else:command+=['artifacts/rv32/fibonacci.elf','--steps','1000','--seconds','60']
        command+=['--server',backend,'--output',str(relative)]
        if fast:command+=['--client','native','--pipeline','--optimize','--packed','--blocks','--sequencer','native']
        job={'id':ident,'program':program,'backend':backend,'fast':fast,'phase':'starting','started':time.time(),'output':out}
        save_job(job)
        thread=threading.Thread(target=work,args=(job,command),daemon=True);job['thread']=thread;thread.start()
        return ident

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,data,typ='application/json'):
        if typ=='application/json':data=json.dumps(data).encode()
        self.send_response(code);self.send_header('Content-Type',typ);self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def authenticated(self):
        if not ACCESS_TOKEN:return False
        try:
            cookies=SimpleCookie(self.headers.get('Cookie',''))
            return 'dnsdemo' in cookies and hmac.compare_digest(cookies['dnsdemo'].value,ACCESS_TOKEN)
        except Exception:return False
    def do_GET(self):
        query=parse_qs(urlparse(self.path).query,keep_blank_values=True)
        if 'access' in query:
            token=query['access'][0]
            valid=bool(ACCESS_TOKEN and hmac.compare_digest(token,ACCESS_TOKEN))
            self.send_response(303);self.send_header('Location','/');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
            secure='; Secure' if self.headers.get('X-Forwarded-Proto')=='https' else ''
            cookie=f'dnsdemo={ACCESS_TOKEN}' if valid else 'dnsdemo=; Max-Age=0'
            self.send_header('Set-Cookie',cookie+f'; Path=/; HttpOnly; SameSite=Lax{secure}');self.end_headers();return
        path=urlparse(self.path).path
        if path=='/':return self.send(200,Path(__file__).with_name('demo.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/status':return self.send(200,{**status(),'can_control':self.authenticated()})
        if path in ('/api/image','/api/report','/api/result'):
            with lock:out=job['output'] if job else None
            if out:
                if path=='/api/result':return self.send(200,read(out/'run.json') or {'status':'running'})
                file=out/('index.html' if path.endswith('report') else 'dns-view.png')
                if path.endswith('image') and not file.exists():file=out/'preview.png'
                if file.exists():
                    if path.endswith('report'):
                        import re
                        page=file.read_text().replace('href="./run.json"','href="/api/result"').replace('href="./dns-view.png"','href="/api/image"')
                        page=re.sub(r'<a href="\.\./[^\"]+">([^<]+)</a>',r'\1',page)
                        return self.send(200,page.encode(),'text/html')
                    return self.send(200,file.read_bytes(),'image/png')
        self.send(404,{'error':'Not found'})
    def do_POST(self):
        if not self.authenticated():return self.send(403,{'error':'Demo access required'})
        origin=self.headers.get('Origin')
        if origin and urlparse(origin).netloc!=self.headers.get('Host'):return self.send(403,{'error':'Origin mismatch'})
        if self.path=='/api/stop':
            with lock:
                item=job
                if item and item['phase'] in ('starting','running'):
                    item['phase']='stopped';save_job(item)
                else:return self.send(409,{'error':'Nothing is running'})
            return self.send(200,{'stopped':True})
        if self.path!='/api/run':return self.send(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1024:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(size));program=data.get('program','doom');backend=data.get('backend','bind');fast=data.get('fast',True)
            if program not in ('doom','fibonacci') or backend not in ('bind','knot') or not isinstance(fast,bool):raise ValueError('Invalid run configuration')
            if program=='doom' and backend=='knot':raise ValueError('Knot is available for the small demo; full-zone update latency is too high for a useful live demo.')
            ident=start(program,backend,fast);self.send(202,{'id':ident})
        except (ValueError,json.JSONDecodeError) as e:self.send(400,{'error':str(e)})

def main():
    global job
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8767);p.add_argument('--host',default='127.0.0.1');p.add_argument('--last-run');a=p.parse_args()
    if a.last_run:
        out=Path(a.last_run).resolve();r=read(out/'run.json')
        if r:
            job={'id':out.name,'program':'fibonacci' if 'results' in r else 'doom','backend':r['backend'].split()[-1].lower(),
                 'fast':bool(r.get('optimization',{}).get('blocks')),'phase':'complete','started':out.stat().st_mtime-r['elapsed'],
                 'finished':out.stat().st_mtime,'exit_code':0,'output':out}
    saved=sorted((ROOT/'artifacts/live').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    if saved:job=restore_job(saved[0].parent) or job
    print(f'Live DNS demo: http://localhost:{a.port}',flush=True)
    ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()

if __name__=='__main__':main()
