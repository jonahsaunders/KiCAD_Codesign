import json
import threading
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch
from PIL import Image
from kicad_codesign.protocol import Action, parse_decision, ProtocolError, validate_key
from kicad_codesign.providers import (Connection, OpenAIProvider, ClaudeProvider, CompatibleProvider,
                                  ProviderError, request_decision, validate_base_url)
from kicad_codesign.desktop import annotate, image_changed, Snapshot, verify_snapshot, DesktopError

def decision(kind='click',**kw):
    action=Action(kind=kind,**({'x':500,'y':500} if kind in {'click','move','drag','scroll'} else {}),**kw)
    return {'status':'act','summary':'Inspect the resistor','plan':['Inspect its properties'],'action':action.to_dict()}

DONE={'status':'done','summary':'The visible change is complete.','plan':[],'action':None}

class ProtocolTests(unittest.TestCase):
    def test_click(self):self.assertEqual(parse_decision(decision()).action.x,500)
    def test_done(self):self.assertIsNone(parse_decision(DONE).action)
    def test_done_cannot_mutate(self):
        with self.assertRaises(ProtocolError):parse_decision({**DONE,'action':Action('key',key='r').to_dict()})
    def test_arbitrary_code_rejected(self):
        with self.assertRaises(ProtocolError):parse_decision(decision('execute',text='print(1)'))
    def test_bad_coords(self):
        for x in (-1,1001,float('nan'),float('inf'),True,'500'):
            with self.subTest(x=x),self.assertRaises(ProtocolError):
                raw=decision();raw['action']['x']=x;parse_decision(raw)
    def test_extra_fields(self):
        raw=decision();raw['action']['command']='cmd'
        with self.assertRaises(ProtocolError):parse_decision(raw)
    def test_field_mismatch(self):
        raw=decision();raw['action']['text']='unexpected'
        with self.assertRaises(ProtocolError):parse_decision(raw)
    def test_no_unbounded_sleep(self):
        with self.assertRaises(ProtocolError):parse_decision(decision('wait',seconds=30))
    def test_no_multiline_text(self):
        with self.assertRaises(ProtocolError):parse_decision(decision('type',text='x\nrun'))
    def test_unicode_text(self):self.assertEqual(parse_decision(decision('type',text='10 kΩ')).action.text,'10 kΩ')
    def test_allowed_keys(self):
        for key in ['e','m','r','ctrl+a','ctrl+z','shift+tab','escape']:validate_key(key)
    def test_forbidden_keys(self):
        for key in ['win+r','alt+f4','ctrl+s','ctrl+shift+s','ctrl+o','ctrl+n','ctrl+w','alt+tab','f12','ctrl+alt+delete']:
            with self.subTest(key=key),self.assertRaises(ProtocolError):validate_key(key)
    def test_scroll_limit(self):
        with self.assertRaises(ProtocolError):parse_decision(decision('scroll',delta=100))

class ProviderTests(unittest.TestCase):
    def test_openai_payload(self):
        url,headers,body=OpenAIProvider().build_request(Connection(api_key='test-only'),'goal',[('current',b'PNG')])
        self.assertEqual(url,'https://api.openai.com/v1/responses');self.assertFalse(body['store'])
        self.assertTrue(body['tools'][0]['strict']);self.assertIn('input_image',str(body['input']))
        self.assertNotIn('test-only',json.dumps(body))
    def test_claude_payload(self):
        url,headers,body=ClaudeProvider().build_request(Connection(provider='Claude',model='claude-sonnet-4-6',api_key='test-only'),'goal',[('current',b'PNG')])
        self.assertEqual(url,'https://api.anthropic.com/v1/messages');self.assertEqual(headers['anthropic-version'],'2023-06-01')
        self.assertTrue(body['tool_choice']['disable_parallel_tool_use'])
        self.assertEqual(body['messages'][0]['content'][-1]['source']['media_type'],'image/png')
    def test_openai_decode(self):
        result=OpenAIProvider().decode_response({'output':[{'type':'function_call','name':'submit_decision','arguments':json.dumps(DONE)}]})
        self.assertEqual(result.status,'done')
    def test_claude_decode(self):
        result=ClaudeProvider().decode_response({'content':[{'type':'tool_use','name':'submit_decision','input':decision()}]})
        self.assertEqual(result.action.kind,'click')
    def test_rejects_ambiguous_tool_calls(self):
        call={'type':'function_call','name':'submit_decision','arguments':json.dumps(DONE)}
        with self.assertRaises(ProviderError):OpenAIProvider().decode_response({'output':[call,call]})
    def test_no_key(self):
        with self.assertRaises(ProviderError):request_decision(Connection(),'goal',[])
    def test_url_validation(self):
        for url in ['http://example.com/v1','https://user:secret@example.com','https://example.com?key=x','file:///local','https://example.com/#x']:
            with self.subTest(url=url),self.assertRaises(ProviderError):validate_base_url(url)
        self.assertEqual(validate_base_url('http://localhost:11434/v1/'),'http://localhost:11434/v1')
    def test_local_http_roundtrip(self):
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                payload=json.dumps({'choices':[{'message':{'tool_calls':[{'function':{'name':'submit_decision','arguments':json.dumps(DONE)}}]}}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):pass
        server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            c=Connection('OpenAI-compatible','fixture','',f'http://127.0.0.1:{server.server_port}/v1')
            self.assertEqual(request_decision(c,'fixture task',[('current',b'PNG')]).status,'done')
            self.assertEqual(received[0][0],'/v1/chat/completions');self.assertIn('data:image/png;base64,',json.dumps(received[0][1]))
        finally:server.shutdown();server.server_close();thread.join()

class ViewTests(unittest.TestCase):
    def test_annotation_does_not_change_original(self):
        source=Image.new('RGB',(400,300),'white');marked=annotate(source,[[(.1,.1),(.8,.1),(.8,.8),(.1,.8)]])
        self.assertEqual(source.getpixel((40,30)),(255,255,255));self.assertNotEqual(marked.getpixel((40,30)),(255,255,255))
    def test_changed_view(self):self.assertTrue(image_changed(Image.new('RGB',(400,300),'white'),Image.new('RGB',(400,300),'black')))
    def test_unchanged_view(self):
        a=Image.new('RGB',(400,300),'white');self.assertFalse(image_changed(a,a.copy()))
    def test_resize_invalidates(self):
        a=Snapshot(1,(0,0,400,300),Image.new('RGB',(400,300)),'A')
        b=Snapshot(1,(1,0,401,300),a.image,'A')
        with patch('kicad_codesign.desktop.capture',return_value=b),self.assertRaises(DesktopError):verify_snapshot(None,a)
    def test_different_dialog_invalidates(self):
        a=Snapshot(1,(0,0,400,300),Image.new('RGB',(400,300)),'A')
        b=Snapshot(2,a.rect,a.image,'Properties')
        with patch('kicad_codesign.desktop.capture',return_value=b),self.assertRaises(DesktopError):verify_snapshot(None,a)

if __name__=='__main__':unittest.main()
