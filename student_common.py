# student_common.py (fixed)
import time
import threading
import xmlrpc.client
from xmlrpc.server import SimpleXMLRPCServer
from socketserver import ThreadingMixIn
from typing import Dict, Set
import sys
import http.client
import datetime

SERVER_URL = "http://127.0.0.1:9000/"
RPC_TIMEOUT = 5.0
LOCAL_HOST = "127.0.0.1"
PROBE_PORTS = range(9101, 9111)

class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout=RPC_TIMEOUT):
        super().__init__()
        self._timeout = timeout
    def make_connection(self, host):
        return http.client.HTTPConnection(host, timeout=self._timeout)

def new_server_proxy(timeout=RPC_TIMEOUT):
    return xmlrpc.client.ServerProxy(SERVER_URL, allow_none=True, transport=TimeoutTransport(timeout))

def new_peer_proxy(url: str, timeout=RPC_TIMEOUT):
    try:
        return xmlrpc.client.ServerProxy(url, allow_none=True, transport=TimeoutTransport(timeout))
    except Exception:
        return xmlrpc.client.ServerProxy(url, allow_none=True)

class ThreadingXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True

# State
my_roll: str = None
my_url: str = None

_clock_lock = threading.Lock()
_clock = 0
def tick():
    global _clock
    with _clock_lock:
        _clock += 1
        return _clock

def update_clock(ts):
    global _clock
    with _clock_lock:
        _clock = max(_clock, int(ts)) + 1
        return _clock

_peers_lock = threading.Lock()
peers: Dict[str,str] = {}

# RA state
requesting = False
in_cs = False
my_ts = None
ok_received: Set[str] = set()
deferred: Set[str] = set()

# Events for synchronization
ask_request_event = threading.Event()
enter_cs_event = threading.Event()

_print_lock = threading.Lock()
def _log(msg):
    with _print_lock:
        print(f"[{datetime.datetime.now()}] {msg}", flush=True)

# ---------------- peer RPCs ----------------
def receive_request(from_roll: str, ts):
    from_roll = str(from_roll)
    try:
        ts_i = int(float(ts))
    except Exception:
        ts_i = int(time.time() * 1000000)

    try:
        update_clock(ts_i)
    except Exception:
        pass

    should_defer = False
    if in_cs:
        should_defer = True
    elif requesting and my_ts is not None:
        try:
            left = (int(my_ts), int(my_roll))
            right = (int(ts_i), int(from_roll))
            if left < right:
                should_defer = True
        except Exception:
            pass

    if should_defer:
        deferred.add(from_roll)
        _log(f"[Student {my_roll}] Deferred request from {from_roll} (req ts={ts_i}) — will grant after I exit CS.")
    else:
        url = peers.get(from_roll)
        if not url:
            _refresh_peers_quiet()
            url = peers.get(from_roll)
        if url:
            try:
                p = new_peer_proxy(url)
                p.receive_ok(my_roll)
                _log(f"[Student {my_roll}] Sent OK to {from_roll}")
            except Exception as e:
                _log(f"[Student {my_roll}] ERROR sending OK to {from_roll}: {e}")
        else:
            _log(f"[Student {my_roll}] WARNING: no URL for {from_roll}, cannot send OK")
    return True

def receive_ok(from_roll: str):
    from_roll = str(from_roll)
    ok_received.add(from_roll)
    total_needed = max(0, len(peers) - 1)
    _log(f"[Student {my_roll}] Received OK from {from_roll} ({len(ok_received)}/{total_needed})")

    try:
        srv = new_server_proxy()
        srv.ok_signal(from_roll, my_roll)
    except Exception:
        pass

    if len(ok_received) >= total_needed:
        enter_cs_event.set()
    return True

def receive_release(from_roll: str):
    _log(f"[Student {my_roll}] Received RELEASE notice from {from_roll}")
    return True

def ping():
    return True

# ---------------- server RPCs ----------------
def ask_to_request():
    print(f"\n[Student {my_roll}] ✅ MCQ exam auto-submitted. Press ENTER to exit MCQ screen.")
    print(f"[Student {my_roll}] Now you can choose whether to enter ISA marks.")
    ask_request_event.set()
    return True


def notify_selection(target_roll):
    _log(f"[Student {my_roll}] notify_selection({target_roll}) received (legacy fallback).")
    return True

def grant_write():
    _log(f"[Student {my_roll}] grant_write() called by server (legacy).")
    enter_cs_event.set()
    return True

def isa_phase_done(path):
    _log(f"[Student {my_roll}] ISA phase done. Excel at {path}")
    return True

# ---------------- MCQ worker ----------------
_mcq_done = threading.Event()
_mcq_answers_local: Dict[int,int] = {}
# Event set when server calls start_mcq() on this student (server push)
_mcq_start_event = threading.Event()

def notify_mcq_submitted():
    """Called by server when exam auto-submits this student."""
    _log(f"[Student {my_roll}] Received notification: MCQ EXAM auto-submitted by server, please press ENTER to exit exam hall.")
    _mcq_done.set()
    return True

def _mcq_worker():
    srv = new_server_proxy()
    _log(f"[Student {my_roll}] MCQ worker starting; waiting for MCQ to be active...")
    while True:
        if _mcq_done.is_set():
            return
        try:
            # Wake up either if server reports active OR server pushed start via start_mcq RPC
            if _mcq_start_event.is_set():
                break
            if srv.get_mcq_active():
                break
        except Exception as e:
            _log(f"[Student {my_roll}] WARN contacting server for MCQ active: {e}")
        time.sleep(0.5)

    for qnum in range(1, 11):
        if _mcq_done.is_set():
            return

        try:
            q = srv.get_question_for_student(my_roll, qnum)
        except Exception as e:
            _log(f"[Student {my_roll}] ERROR fetching question {qnum}: {e}")
            time.sleep(0.5)
            q = {}
        if not q:
            _log(f"[Student {my_roll}] No question data for q{qnum}; skipping.")
            chosen = 0
            _mcq_answers_local[qnum] = chosen
            try:
                srv.submit_mcq_answer(my_roll, qnum, chosen)
            except Exception:
                pass
            continue

        _log(f"[Student {my_roll}] Q{qnum}: {q['q']}")
        for idx, opt in enumerate(q['options'], start=1):
            print(f"{idx}) {opt}")
        ans = input(f"[Student {my_roll}] Enter option number (1-4) or press Enter to skip: ").strip()
        chosen = 0
        if ans.isdigit():
            try:
                v = int(ans)
                if 1 <= v <= 4:
                    chosen = v
            except Exception:
                chosen = 0
        if chosen == 0 and not _mcq_done.is_set():
            _log(f"[Student {my_roll}] Skipped Q{qnum}")
        else:
            _log(f"[Student {my_roll}] Answered Q{qnum} -> {chosen}")

        _mcq_answers_local[qnum] = chosen
        try:
            srv.submit_mcq_answer(my_roll, qnum, chosen)
        except Exception as e:
            _log(f"[Student {my_roll}] WARN submit_mcq_answer failed: {e}")

    if _mcq_done.is_set():
        return

    _log(f"[Student {my_roll}] Completed local answering of 10 questions.")
    confirm = input(f"[Student {my_roll}] Submit test now? (Enter y): ").strip().lower()
    if confirm.startswith('y'):
        try:
            srv.submit_mcq_final(my_roll)
            print("\nTest Submitted.")
            _mcq_done.set()
        except Exception as e:
            _log(f"[Student {my_roll}] ERROR submit_mcq_final: {e}")
    else:
        _log(f"[Student {my_roll}] Chose not to submit immediately; will be auto-submitted on timeout.")

# ---------------- RA initiation ----------------
def _start_ra_request():
    global requesting, my_ts, ok_received, deferred, in_cs
    try:
        srv = new_server_proxy()
        reg = srv.get_registry()
        if isinstance(reg, dict) and reg:
            with _peers_lock:
                peers.clear()
                peers.update({str(k): str(v) for k, v in reg.items()})
            _log(f"[Student {my_roll}] Registry fetched for RA: {list(peers.keys())}")
        else:
            _log(f"[Student {my_roll}] server.get_registry returned empty/invalid; using current peers map.")
    except Exception as e:
        _log(f"[Student {my_roll}] WARN: could not fetch registry: {e} ; using current peers map.")

    with _peers_lock:
        targets = {r: u for r, u in peers.items() if r != my_roll}

    my_ts = tick()
    requesting = True
    ok_received.clear()
    deferred.clear()

    try:
        srv = new_server_proxy()
        srv.register_intent(my_roll, int(my_ts))
    except Exception:
        _log(f"[Student {my_roll}] WARN: could not register intent with server")

    _log(f"[Student {my_roll}] REQUEST(ts={my_ts}) -> targets {list(targets.keys())}")
    for r, url in targets.items():
        try:
            p = new_peer_proxy(url)
            p.receive_request(my_roll, int(my_ts))
        except Exception as e:
            _log(f"[Student {my_roll}] WARN: REQUEST failed to {r}: {e}")

    needed = set(targets.keys())
    _log(f"[Student {my_roll}] Waiting for OKs from: {needed}")
    while True:
        missing = needed - ok_received
        if not missing:
            break
        _log(f"[Student {my_roll}] Still waiting for OKs from: {missing}")
        time.sleep(5)

    in_cs = True
    _log(f"[Student {my_roll}] All OKs received ({len(ok_received)}/{len(needed)}). Entering CS.")
    enter_cs_event.set()

def _main_prompt_loop():
    global requesting, in_cs, my_ts, deferred
    mcq_thread = threading.Thread(target=_mcq_worker, daemon=True)
    mcq_thread.start()

    while True:
        ask_request_event.wait()
        ask_request_event.clear()
        ans = input(f"[Student {my_roll}] Server asks: Do you want to enter ISA marks? (y/n): ").strip().lower()
        if not ans or ans[0] != 'y':
            _log(f"[Student {my_roll}] Chose NOT to enter ISA now.")
            continue
        t = threading.Thread(target=_start_ra_request, daemon=True)
        t.start()
        _log(f"[Student {my_roll}] Waiting to be allowed to enter critical section...")
        enter_cs_event.wait()
        enter_cs_event.clear()

        # ✨ Clear ISA entry banner
        print("\n==============================")
        print(f"[Student {my_roll}] >>> ENTER ISA MARKS <<<")
        print("==============================\n")

        try:
            raw = input(f"[Student {my_roll}] ISA Marks (integer): ").strip()
            marks = int(raw)
        except Exception as e:
            _log(f"[Student {my_roll}] Invalid marks input: {e}; aborting this attempt.")
            _send_deferred_oks()
            requesting = False
            in_cs = False
            my_ts = None
            ok_received.clear()
            deferred.clear()
            continue

        try:
            srv = new_server_proxy()
            srv.update_isa(my_roll, marks)
            _log(f"[Student {my_roll}] Sent update_isa to server: {marks}")
        except Exception as e:
            _log(f"[Student {my_roll}] ERROR sending update_isa: {e}")

        _send_deferred_oks()

        requesting = False
        in_cs = False
        my_ts = None
        ok_received.clear()
        deferred.clear()
        _log(f"[Student {my_roll}] Completed an ISA entry cycle.")

def _send_deferred_oks():
    with _peers_lock:
        targets = list(deferred)
        try:
            srv = new_server_proxy()
            reg = srv.get_registry()
            if isinstance(reg, dict) and reg:
                peers.clear()
                peers.update({str(k): str(v) for k, v in reg.items()})
                _log(f"[Student {my_roll}] Refreshed peers before flushing deferred OKs: {list(peers.keys())}")
        except Exception:
            pass

    for r in targets:
        url = peers.get(r)
        if not url:
            _log(f"[Student {my_roll}] Cannot send deferred OK to {r}: no URL known")
            continue
        try:
            p = new_peer_proxy(url)
            p.receive_ok(my_roll)
            _log(f"[Student {my_roll}] Sent deferred OK to {r}")
        except Exception as e:
            _log(f"[Student {my_roll}] ERROR sending deferred OK to {r}: {e}")

def show_results(data):
    print("\n===== FINAL RESULTS =====")
    print("Roll | Name       | Marks | MCQ | ISA")
    print("--------------------------------------")
    for row in data:
        roll, name, marks, mcq, isa = row
        print(f"{roll:<4} | {name:<10} | {marks:<5} | {mcq:<3} | {isa}")
    print("==========================\n")
    return True


def _refresh_peers_quiet():
    try:
        srv = new_server_proxy()
        reg = srv.get_registry()
        if isinstance(reg, dict) and reg:
            with _peers_lock:
                peers.clear()
                peers.update({str(k): str(v) for k, v in reg.items()})
            return True
    except Exception:
        return False
    return False

def _refresh_peers():
    ok = _refresh_peers_quiet()
    if ok:
        _log(f"[Student {my_roll}] Peers refreshed from server: {list(peers.keys())}")
        return
    _log(f"[Student {my_roll}] Server registry not available; probing local ports...")
    probed = {}
    for p in PROBE_PORTS:
        url = f"http://127.0.0.1:{p}/"
        if url == my_url: continue
        try:
            proxy = new_peer_proxy(url)
            try:
                proxy.ping()
                roll_guess = str(p - 9100)
                probed[roll_guess] = url
            except Exception:
                pass
        except Exception:
            pass
    probed[str(my_roll)] = my_url
    with _peers_lock:
        peers.clear()
        peers.update(probed)
    _log(f"[Student {my_roll}] Probed peers: {list(peers.keys())}")

# ---------------- RPC callbacks the server expects ----------------
def start_mcq():
    """Server calls this to tell the student to begin MCQ exam."""
    _log(f"[Student {my_roll}] start_mcq() called by server (push).")
    # Signal the local MCQ worker in case it is waiting on this server-side push.
    _mcq_start_event.set()
    return True

def send_time():
    """Server calls this during Berkeley sync to get the student's time."""
    # Best-effort: try to compute a CV relative to the server's reported time.
    try:
        srv = new_server_proxy()
        server_time_str = srv.get_time()
        if server_time_str:
            # parse server time "HH-MM-SS"
            try:
                server_time = datetime.datetime.strptime(server_time_str, "%H-%M-%S").time()
                # use local system time for student (no explicit input_time mechanism here)
                now = datetime.datetime.now().time()
                # compute seconds difference (approx)
                server_seconds = server_time.hour*3600 + server_time.minute*60 + server_time.second
                local_seconds = now.hour*3600 + now.minute*60 + now.second
                cv = float(local_seconds - server_seconds)
            except Exception:
                cv = 0.0
        else:
            cv = 0.0
    except Exception:
        cv = 0.0

    # Send CV back to server (best-effort)
    try:
        srv = new_server_proxy()
        srv.receive_cv(my_roll, cv)
    except Exception:
        pass
    _log(f"[Student {my_roll}] send_time() called by server; reported CV={cv}")
    return True

# ---------------- RPC server ----------------
def _run_rpc_server(host, port):
    srv = ThreadingXMLRPCServer((host, port), allow_none=True, logRequests=False)
    srv.register_function(receive_request, "receive_request")
    srv.register_function(receive_ok, "receive_ok")
    srv.register_function(receive_release, "receive_release")
    srv.register_function(ping, "ping")
    srv.register_function(ask_to_request, "ask_to_request")
    srv.register_function(notify_selection, "notify_selection")
    srv.register_function(grant_write, "grant_write")
    srv.register_function(isa_phase_done, "isa_phase_done")
    srv.register_function(show_results, "show_results")
    srv.register_function(start_mcq,"start_mcq")
    srv.register_function(send_time,"send_time")

    srv.register_function(notify_mcq_submitted, "notify_mcq_submitted")
    _log(f"[Student {my_roll}] RPC server running at {host}:{port}")
    srv.serve_forever()

def main(roll: str, host: str, port: int):
    global my_roll, my_url
    my_roll = str(roll)
    my_url = f"http://{host}:{int(port)}/"
    threading.Thread(target=_run_rpc_server, args=(host, int(port)), daemon=True).start()
    time.sleep(0.05)
    try:
        srv = new_server_proxy()
        srv.register_student(my_roll, my_url)
        _log(f"[Student {my_roll}] Registered with server.")
    except Exception as e:
        _log(f"[Student {my_roll}] WARN: register_student failed: {e}")
    time.sleep(0.1)
    _refresh_peers()
    _log(f"[Student {my_roll}] Main prompt loop starting (this thread handles user input).")
    _main_prompt_loop()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python student_common.py <HOST> <PORT>")
        sys.exit(1)
    main("X", sys.argv[1], int(sys.argv[2]))
