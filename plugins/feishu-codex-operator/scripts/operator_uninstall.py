"""Read-only teardown checks and explicit one-shot routing detachment. No tasks."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from urllib.request import Request, ProxyHandler, build_opener

import routing_cli
from operator_core import model_router_config as settings


def assert_port_free(port):
    with socket.socket() as listener:
        if os.name == 'nt': listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', port))


def service(state, port, *, stop=False):
    request = Request(settings.url(state, port) + '/lifecycle', method='POST' if stop else 'GET',
                      data=b'' if stop else None)
    with build_opener(ProxyHandler({}), settings._NoRedirect()).open(request, timeout=3) as response:
        raw = response.read(16385)
    if len(raw) > 16384: raise ValueError('invalid_router_status')
    value = settings.loads(raw)
    expected = hashlib.sha256((str((state.parent/'operator_model_router.py').resolve()) + '\n' + str(state.resolve())).encode()).hexdigest()
    if value.get('service') != expected: raise ValueError('router_identity_mismatch')
    return value


def inspect(project, config, port):
    runtime = project/'.codex/feishu-codex-operator-runtime'
    state = runtime/'model-router'
    pending = 0
    if (runtime/'callbacks.sqlite3').exists():
        with closing(sqlite3.connect((runtime/'callbacks.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
            pending = db.execute("select count(*) from final_callback_requests where state in ('pending','captured')").fetchone()[0]
    entry = state/'codex-entry.json'
    owned_entry = entry.exists()
    if owned_entry:
        journal = settings.read_registration(entry)
        expected = {'config':str(config.resolve()), 'block':settings.BEGIN + 'openai_base_url = ' + json.dumps(settings.url(state,port)) + '\n' + settings.END}
        if journal != expected or not config.read_bytes().startswith(journal['block'].encode()):
            raise ValueError('router_entry_ownership_conflict')
    try:
        assert_port_free(port)
        router = {'status':'stopped'}
    except OSError:
        router = service(state,port)
    active = 0 if router['status']=='stopped' else router['diagnostics']['timing']['active']
    if not isinstance(active,int) or active < 0: raise ValueError('invalid_router_activity')
    callback = routing_cli._status(runtime)
    return {'pending_callbacks':pending,'active_router_requests':active,
            'router_status':router['status'],'owned_router_entry':owned_entry,
            'owned_callback_registration':callback['matches_runtime'],
            'other_callback_registration_preserved':callback['configured'] and not callback['matches_runtime']}


def detach(project, config, port):
    observed = inspect(project,config,port)
    if observed['pending_callbacks'] or observed['active_router_requests']:
        raise ValueError('finish_callbacks_and_router_requests_before_uninstall')
    runtime = project/'.codex/feishu-codex-operator-runtime'; state = runtime/'model-router'
    if observed['owned_router_entry']: settings.deactivate(state,config)
    if observed['router_status'] != 'stopped':
        service(state,port,stop=True)  # One stop only; never retry this request.
        deadline = time.monotonic()+5
        while True:
            try: assert_port_free(port); break
            except OSError:
                if time.monotonic() >= deadline: raise ValueError('router_stop_not_confirmed')
                time.sleep(.1)  # Listener release check, not model/request replay.
    if observed['owned_callback_registration']: routing_cli._unregister(runtime)
    return {'routing_detached':True,'model_requests_replayed':0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','detach'])
    parser.add_argument('--project-root',required=True,type=Path)
    parser.add_argument('--codex-config',required=True,type=Path)
    parser.add_argument('--port',type=int,default=4317)
    args = parser.parse_args()
    try:
        if not 1024 <= args.port <= 65535: raise ValueError('invalid_router_port')
        operation = inspect if args.action=='inspect' else detach
        print(json.dumps(operation(args.project_root.resolve(),args.codex_config.resolve(),args.port)))
    except Exception:
        # Never print config contents, tokens, callback material or credentialed URLs.
        print(json.dumps({'error':'uninstall_preflight_or_detach_failed','retried':False}))
        raise SystemExit(1)
