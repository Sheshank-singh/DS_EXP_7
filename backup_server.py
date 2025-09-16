# backup_server.py – receives forwarded MCQ submissions
import time, datetime
from xmlrpc.server import SimpleXMLRPCServer
from socketserver import ThreadingMixIn
import xmlrpc.client

MCQ_QUESTIONS={1:{"answer":2},2:{"answer":2},3:{"answer":2},4:{"answer":3},5:{"answer":2},
6:{"answer":2},7:{"answer":4},8:{"answer":3},9:{"answer":3},10:{"answer":2}}
MAIN_SERVER="http://127.0.0.1:9000/"

class ThreadingXMLRPCServer(ThreadingMixIn,SimpleXMLRPCServer):daemon_threads=True

def _compute(answers,flags):
    raw=0
    for qnum,qdef in MCQ_QUESTIONS.items():
        if int(answers.get(qnum,0)or 0)==qdef["answer"]:raw+=10
    if flags>=2:final=0
    elif flags==1:final=int(raw*0.8)
    else:final=raw
    return raw,final

def process_forwarded_submission(roll,answers,flags):
    print(f"[Backup] got forwarded roll {roll}")
    time.sleep(1.5)
    raw,final=_compute(answers or {},int(flags or 0))
    print(f"[Backup] done roll {roll} final={final} -> notify main")
    xmlrpc.client.ServerProxy(MAIN_SERVER,allow_none=True).backup_result(str(roll),int(final))
    return True

def run_backup():
    srv=ThreadingXMLRPCServer(("0.0.0.0",9010),allow_none=True,logRequests=False)
    srv.register_function(process_forwarded_submission,"process_forwarded_submission")
    print("[Backup] running on 9010 ...")
    srv.serve_forever()

if __name__=="__main__":
    run_backup()
