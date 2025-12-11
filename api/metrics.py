"""
Metrics endpoint for monitoring and analytics
"""
import json
import os
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    """Metrics handler for operational monitoring"""
    
    # In production, these would be stored in Redis or a time-series database
    # For now, using in-memory storage (resets on each deployment)
    _metrics_cache = {
        'webhook_requests': 0,
        'webhook_success': 0,
        'webhook_failures': 0,
        'verification_requests': 0,
        'forward_retries': 0,
        'avg_response_time_ms': 0,
        'last_request_time': None,
        'uptime_start': datetime.utcnow().isoformat()
    }
    
    def do_GET(self):
        """Return operational metrics"""
        metrics_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'service': 'lyo-webhook',
            'environment': os.environ.get('VERCEL_ENV', 'production'),
            'region': os.environ.get('VERCEL_REGION', 'iad1'),
            'metrics': {
                'requests': {
                    'total': self._metrics_cache['webhook_requests'],
                    'success': self._metrics_cache['webhook_success'],
                    'failures': self._metrics_cache['webhook_failures'],
                    'success_rate': self._calculate_success_rate()
                },
                'verification': {
                    'total': self._metrics_cache['verification_requests']
                },
                'performance': {
                    'avg_response_time_ms': self._metrics_cache['avg_response_time_ms'],
                    'forward_retries': self._metrics_cache['forward_retries']
                },
                'availability': {
                    'uptime_seconds': self._calculate_uptime(),
                    'last_request': self._metrics_cache['last_request_time'],
                    'status': 'operational'
                }
            },
            'configuration': {
                'webhook_verify_token_set': bool(os.environ.get('WEBHOOK_VERIFY_TOKEN')),
                'hetzner_server_configured': bool(os.environ.get('HETZNER_SERVER_URL')),
                'monitoring_enabled': os.environ.get('MONITORING_ENABLED', 'true').lower() == 'true',
                'max_retries': int(os.environ.get('MAX_RETRIES', '3'))
            }
        }
        
        # Send response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        
        self.wfile.write(json.dumps(metrics_data, indent=2).encode('utf-8'))
    
    def _calculate_success_rate(self):
        """Calculate success rate percentage"""
        total = self._metrics_cache['webhook_requests']
        if total == 0:
            return 100.0
        
        success = self._metrics_cache['webhook_success']
        return round((success / total) * 100, 2)
    
    def _calculate_uptime(self):
        """Calculate uptime in seconds"""
        try:
            start_time = datetime.fromisoformat(self._metrics_cache['uptime_start'])
            uptime = datetime.utcnow() - start_time
            return int(uptime.total_seconds())
        except:
            return 0
    
    @classmethod
    def update_metric(cls, metric_name: str, value: any):
        """Update a metric value (called from webhook handler)"""
        if metric_name in cls._metrics_cache:
            if isinstance(cls._metrics_cache[metric_name], int):
                cls._metrics_cache[metric_name] += value if isinstance(value, int) else 1
            else:
                cls._metrics_cache[metric_name] = value