import xmlrpc.client
import random
import time
import datetime
from xmlrpc.server import SimpleXMLRPCServer
import threading
from socketserver import ThreadingMixIn

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9000
TEACHER_HOST = "127.0.0.1"
TEACHER_PORT = 9001

class ThreadingXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True

roll_numbers = ["1", "2", "3", "4", "5"]

server_proxy = xmlrpc.client.ServerProxy(f"http://{SERVER_HOST}:{SERVER_PORT}/", allow_none=True)
teacher_proxy = xmlrpc.client.ServerProxy(f"http://{TEACHER_HOST}:{TEACHER_PORT}/", allow_none=True)

local_time = None
exam_start_event = threading.Event()

def input_time():
    global local_time
    user_input = input("[Client] (Step 1) Enter current local client time (HH-MM-SS): ")
    local_time = datetime.datetime.strptime(user_input, "%H-%M-%S")
    print(f"[Client] Local time set to {local_time.strftime('%H-%M-%S')}")
    return True

def calculate_cv(server_time_str):
    global local_time
    server_time = datetime.datetime.strptime(server_time_str, "%H-%M-%S")
    cv = (local_time - server_time).total_seconds()
    print(f"[Client] (Steps 4-5) Calculated CV = {cv} seconds; sending to Server")
    try:
        server_proxy.receive_cv("Client", cv)
    except Exception as e:
        print("[Client] WARN cannot send CV:", e)
    return True

def apply_adjustment(adj):
    global local_time
    local_time = local_time + datetime.timedelta(seconds=adj)
    print(f"[Client] (Step 9) Adjusted local time: {local_time.strftime('%H-%M-%S')}")
    print(f"[Client] Final synchronized time: {local_time.strftime('%H-%M-%S')}")
    return True

def start_exam():
    print("[Client] Received exam start signal from Server. Starting exam when local setup ready...")
    exam_start_event.set()
    return True

def run_client_server():
    server = ThreadingXMLRPCServer(("0.0.0.0", 9002), allow_none=True, logRequests=False)
    server.register_function(input_time, "input_time")
    server.register_function(calculate_cv, "calculate_cv")
    server.register_function(apply_adjustment, "apply_adjustment")
    server.register_function(start_exam, "start_exam")
    print("[Client] XML-RPC server (threaded) running on port 9002...")
    server.serve_forever()

def exam_timer():
    exam_duration = 30  # seconds (5 minutes)
    interval = 10      # seconds
    start_time = time.time()

    active_rolls = roll_numbers.copy()

    while time.time() - start_time < exam_duration and active_rolls:
        roll = random.choice(active_rolls)
        response = None
        try:
            response = server_proxy.cheating_detection(roll)
        except Exception as e:
            print("[Client] WARN cheating_detection RPC failed:", e)

        if response is None:
            try:
                active_rolls.remove(roll)
            except Exception:
                pass
            time.sleep(interval)
            continue

        print(f"[Client] Reporting cheating attempt by roll no: {roll}")
        print(f"[Client] Server response: {response}")
        time.sleep(interval)

    print("\n[Client] Exam finished. Notifying server for exam completion...")
    try:
        server_proxy.exam_completed()
    except Exception as e:
        print("[Client] WARN calling exam_completed:", e)

if __name__ == "__main__":
    t = threading.Thread(target=run_client_server, daemon=True)
    t.start()

    try:
        server_proxy.input_time()
    except Exception:
        pass
    try:
        teacher_proxy.input_time()
    except Exception:
        pass
    input_time()

    try:
        server_proxy.start_synchronization()
    except Exception as e:
        print("[Client] WARN starting synchronization:", e)

    print("[Client] Waiting for exam start signal from Server...")
    exam_start_event.wait()
    exam_timer()
