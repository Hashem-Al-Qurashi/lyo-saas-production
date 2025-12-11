"""
Health check endpoint for monitoring
"""
import json
import time
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler
import urllib.request
import urllib.error

class handler(BaseHTTPRequestHandler):
    """Health check handler with dependency verification"""
    
    def do_GET(self):
        """Return health status with component checks"""
        start_time = time.time()
        health_data = {
            'status': 'healthy',
            'timestamp': datetime.utcnow().isoformat(),
            'service': 'lyo-webhook',
            'version': '1.0.0',
            'checks': {}
        }
        
        # Check environment configuration
        health_data['checks']['configuration'] = self._check_configuration()
        
        # Check Hetzner server connectivity
        health_data['checks']['hetzner_server'] = self._check_hetzner_server()
        
        # Calculate overall health
        all_healthy = all(
            check.get('status') == 'healthy' 
            for check in health_data['checks'].values()
        )
        
        health_data['status'] = 'healthy' if all_healthy else 'degraded'
        health_data['response_time_ms'] = int((time.time() - start_time) * 1000)
        
        # Send response
        status_code = 200 if all_healthy else 503
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        
        self.wfile.write(json.dumps(health_data, indent=2).encode('utf-8'))
    
    def _check_configuration(self):
        """Check if all required environment variables are set"""
        required_vars = [
            'WEBHOOK_VERIFY_TOKEN',
            'HETZNER_SERVER_URL'
        ]
        
        missing = [var for var in required_vars if not os.environ.get(var)]
        
        if missing:
            return {
                'status': 'unhealthy',
                'message': f'Missing environment variables: {", ".join(missing)}'
            }
        
        return {
            'status': 'healthy',
            'message': 'All required configuration present'
        }
    
    def _check_hetzner_server(self):
        """Check if Hetzner server is reachable"""
        try:
            hetzner_url = os.environ.get('HETZNER_SERVER_URL', 'http://135.181.249.116:8000')
            health_url = f"{hetzner_url}/health"
            
            req = urllib.request.Request(
                health_url,
                headers={'User-Agent': 'Webhook-Health-Check'}
            )
            
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.getcode() == 200:
                    return {
                        'status': 'healthy',
                        'message': 'Hetzner server is reachable',
                        'response_code': response.getcode()
                    }
                else:
                    return {
                        'status': 'unhealthy',
                        'message': f'Unexpected response code: {response.getcode()}',
                        'response_code': response.getcode()
                    }
                    
        except urllib.error.URLError as e:
            return {
                'status': 'unhealthy',
                'message': f'Cannot reach Hetzner server: {str(e.reason)}',
                'error': str(e)
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'message': f'Health check failed: {str(e)}',
                'error': str(e)
            }