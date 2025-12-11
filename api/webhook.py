"""
Vercel Serverless Webhook for WhatsApp Business API
Simple webhook forwarder to Hetzner production server
"""
from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle WhatsApp webhook verification"""
        # Parse query parameters
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query) if query else {}
        
        mode = params.get('hub.mode', [None])[0]
        token = params.get('hub.verify_token', [None])[0]
        challenge = params.get('hub.challenge', [None])[0]
        
        # Verify webhook
        if mode == 'subscribe' and token == 'lyo_verify_2024':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write((challenge or 'OK').encode())
        else:
            self.send_response(403)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Forbidden')
    
    def do_POST(self):
        """Forward webhook to Hetzner server"""
        try:
            # Read webhook data
            content_length = int(self.headers.get('Content-Length', 0))
            webhook_data = self.rfile.read(content_length)
            
            # Forward to Hetzner server
            hetzner_url = "https://135.181.249.116:8000/webhook"
            
            req = urllib.request.Request(
                hetzner_url,
                data=webhook_data,
                headers={'Content-Type': 'application/json'}
            )
            
            # Disable SSL verification for self-signed cert
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                result = {"status": "forwarded", "code": response.getcode()}
            
            # Return success
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            
        except Exception as e:
            # Return error
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())