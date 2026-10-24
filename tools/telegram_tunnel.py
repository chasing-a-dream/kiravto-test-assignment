"""Optional loopback-only TLS tunnel for this PC's split VPN routing.

Only CONNECT api.telegram.org:443 is accepted. TLS is end-to-end; this
process never decrypts API requests, reads credentials, or logs traffic.
"""
import select
import socket
import socketserver

def permitted(line):
    parts=line.split()
    return len(parts)==3 and parts[0]=='CONNECT' and parts[1].lower()=='api.telegram.org:443' and parts[2] in ('HTTP/1.0','HTTP/1.1')

class Tunnel(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            line=self.rfile.readline(8193)
            if len(line)>8192 or not permitted(line.decode('ascii',errors='replace')):
                self.connection.sendall(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
                return
            size=len(line)
            while True:
                header=self.rfile.readline(8193)
                size+=len(header)
                if size>32768 or not header: return
                if header in (b'\r\n',b'\n'):break
            with socket.create_connection(('api.telegram.org',443),timeout=10) as upstream:
                self.connection.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                for peer in (self.connection,upstream):peer.settimeout(10)
                while True:
                    readable,_,_=select.select([self.connection,upstream],[],[],45)
                    if not readable:return
                    for peer in readable:
                        data=peer.recv(65536)
                        if not data:return
                        (upstream if peer is self.connection else self.connection).sendall(data)
        except (OSError,ValueError):
            return

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def handle_error(self,*args):pass  # Never log client data or exception URLs.

if __name__=='__main__':
    with Server(('127.0.0.1',18766),Tunnel) as server:
        print('Telegram TLS tunnel ready on 127.0.0.1:18766',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
