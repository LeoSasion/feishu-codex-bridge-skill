"""One-shot detach against synthetic loopback lifecycle only, never inference."""
import hashlib
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import operator_uninstall as uninstall
from operator_core import model_router_config as settings


class UninstallRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='operator-uninstall-'); self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve(); self.runtime=self.project/'.codex/feishu-codex-operator-runtime'
        self.state=self.runtime/'model-router'; self.state.mkdir(parents=True)
        (self.state/'token').write_text('a'*64,encoding='ascii')
        self.config=self.project/'config.toml'; self.original=b'model="native-fixture"\n# later user settings\n'
        self.active=0; self.stops=0; self.paths=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self): self.reply(False)
            def do_POST(self): self.reply(True)
            def reply(self,stop):
                owner.paths.append(self.path)
                assert self.path=='/'+'a'*64+'/v1/lifecycle'
                if stop: owner.stops+=1
                identity=hashlib.sha256((str((owner.runtime/'operator_model_router.py').resolve())+'\n'+str(owner.state.resolve())).encode()).hexdigest()
                raw=json.dumps({'service':identity,'pid':os.getpid(),'status':'stopping' if stop else 'ready',
                                'diagnostics':{'timing':{'active':owner.active}}}).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
                if stop: threading.Thread(target=owner.close_server,daemon=True).start()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.close_server)
        self.port=self.server.server_port
        block=settings.BEGIN+'openai_base_url = '+json.dumps(settings.url(self.state,self.port))+'\n'+settings.END
        self.config.write_bytes(block.encode()+self.original)
        (self.state/'codex-entry.json').write_text(json.dumps({'config':str(self.config),'block':block}),encoding='utf-8')
        local=self.project/'local-appdata'
        self.env=patch.dict(os.environ,{'LOCALAPPDATA':str(local)}); self.env.start(); self.addCleanup(self.env.stop)
        self.callback=local/'OpenAI/Codex/feishu-codex-final-callback/registration.json'; self.callback.parent.mkdir(parents=True)

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def test_detach_restores_native_bytes_and_preserves_other_runtime_registration(self):
        original=json.dumps({'schema_version':2,'runtime_dir':str(self.project/'other-runtime')}).encode()
        self.callback.write_bytes(original)
        result=uninstall.detach(self.project,self.config,self.port)
        self.assertTrue(result['routing_detached']); self.assertEqual(self.stops,1)
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertFalse((self.state/'codex-entry.json').exists()); self.assertEqual(self.callback.read_bytes(),original)

    def test_active_request_and_pending_callback_block_before_config_mutation(self):
        before=self.config.read_bytes(); self.active=1
        with self.assertRaises(ValueError): uninstall.detach(self.project,self.config,self.port)
        self.active=0
        with closing(sqlite3.connect(self.runtime/'callbacks.sqlite3')) as db, db:
            db.execute('create table final_callback_requests (state text)'); db.execute("insert into final_callback_requests values ('pending')")
        with self.assertRaises(ValueError): uninstall.detach(self.project,self.config,self.port)
        self.assertEqual(self.config.read_bytes(),before); self.assertEqual(self.stops,0)

    def test_changed_router_prefix_stops_without_touching_user_config(self):
        changed=b'# user changed managed area\n'+self.config.read_bytes(); self.config.write_bytes(changed)
        with self.assertRaises(ValueError): uninstall.detach(self.project,self.config,self.port)
        self.assertEqual(self.config.read_bytes(),changed); self.assertEqual(self.stops,0); self.assertEqual(self.paths,[])


if __name__=='__main__': unittest.main()
