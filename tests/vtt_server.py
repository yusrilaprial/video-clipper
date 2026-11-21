import http.server, threading
VTT = """WEBVTT
Kind: captions
Language: id

00:00:00.000 --> 00:00:01.500 align:start position:0%
 
halo<00:00:00.500><c> semua</c><00:00:01.000><c> selamat</c>

00:00:01.500 --> 00:00:01.510 align:start position:0%
halo semua selamat
 

00:00:01.510 --> 00:00:03.000 align:start position:0%
halo semua selamat
datang<00:00:02.000><c> di</c><00:00:02.500><c> video</c><c> ini</c>

00:00:03.000 --> 00:00:03.010 align:start position:0%
datang di video ini
 

00:00:03.010 --> 00:00:04.800 align:start position:0%
datang di video ini
kita<c> akan</c><c> belajar</c><c> memotong</c><c> video</c>
"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        b=VTT.encode(); self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
def start():
    s=http.server.ThreadingHTTPServer(("127.0.0.1",0),H); threading.Thread(target=s.serve_forever,daemon=True).start(); return s
