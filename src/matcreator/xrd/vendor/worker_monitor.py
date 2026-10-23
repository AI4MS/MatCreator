"""Independent worker heartbeat/outcome; survives loss of an HTTP/Agent request."""
import json,os,subprocess,sys,time
from pathlib import Path

def main():
    root,run=map(Path,sys.argv[1:]);status=run/'worker-status.json'
    def record(**extra):
        value={'supervisor_pid':os.getpid(),'worker_pid':process.pid,'heartbeat_ns':time.time_ns(),**extra}
        temp=status.with_suffix('.tmp');temp.write_text(json.dumps(value),encoding='utf-8');temp.replace(status)
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    process=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('worker.py')),str(root),str(run)],**options)
    while process.poll() is None:
        record(status='running')
        try:process.wait(timeout=3)
        except subprocess.TimeoutExpired:pass
    record(status='completed' if process.returncode==0 else 'failed',exit_code=process.returncode,finished_at_ns=time.time_ns())
    return process.returncode
if __name__=='__main__':sys.exit(main())
