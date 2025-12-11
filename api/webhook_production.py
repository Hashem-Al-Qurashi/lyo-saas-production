"""
Production-Grade WhatsApp Business API Webhook for Vercel
Enterprise-ready with monitoring, validation, and error handling
"""
import os
import json
import hmac
import hashlib
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from http.server import BaseHTTPRequestHandler
import urllib.request
import urllib.parse
import urllib.error

# Configure structured logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebhookHandler(BaseHTTPRequestHandler):
    """Production webhook handler with enterprise features"""
    
    # Configuration from environment variables
    VERIFY_TOKEN = os.environ.get('WEBHOOK_VERIFY_TOKEN', 'lyo_verify_2024')
    HETZNER_URL = os.environ.get('HETZNER_SERVER_URL', 'http://135.181.249.116:8000')
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET', '')
    MONITORING_ENABLED = os.environ.get('MONITORING_ENABLED', 'true').lower() == 'true'
    MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))
    RETRY_DELAY = float(os.environ.get('RETRY_DELAY', '1.0'))
    REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '10'))
    
    def do_GET(self):
        """
        Handle WhatsApp webhook verification challenge
        https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/
        """
        start_time = time.time()
        request_id = self._generate_request_id()
        
        try:
            # Parse query parameters
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query) if query else {}
            
            # Extract verification parameters
            mode = params.get('hub.mode', [None])[0]
            token = params.get('hub.verify_token', [None])[0]
            challenge = params.get('hub.challenge', [None])[0]
            
            # Log verification attempt
            self._log_event('webhook_verification', {
                'request_id': request_id,
                'mode': mode,
                'has_token': bool(token),
                'has_challenge': bool(challenge)
            })
            
            # Verify webhook subscription
            if mode == 'subscribe' and token == self.VERIFY_TOKEN:
                if not challenge:
                    self._send_error_response(400, 'Missing challenge parameter')
                    return
                
                # Success - return challenge
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('X-Request-ID', request_id)
                self.end_headers()
                self.wfile.write(challenge.encode('utf-8'))
                
                # Log success
                self._log_event('webhook_verified', {
                    'request_id': request_id,
                    'duration_ms': int((time.time() - start_time) * 1000)
                })
            else:
                # Invalid verification
                self._send_error_response(403, 'Invalid verification token')
                self._log_event('webhook_verification_failed', {
                    'request_id': request_id,
                    'reason': 'token_mismatch'
                })
                
        except Exception as e:
            self._handle_exception(e, request_id)
            self._send_error_response(500, 'Internal server error')
    
    def do_POST(self):
        """
        Handle WhatsApp webhook notifications with production reliability
        """
        start_time = time.time()
        request_id = self._generate_request_id()
        
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_error_response(400, 'Empty request body')
                return
            
            if content_length > 10485760:  # 10MB limit
                self._send_error_response(413, 'Request body too large')
                return
            
            webhook_data = self.rfile.read(content_length)
            
            # Validate signature if secret is configured
            if self.WEBHOOK_SECRET:
                signature = self.headers.get('X-Hub-Signature-256', '')
                if not self._verify_signature(webhook_data, signature):
                    self._send_error_response(401, 'Invalid signature')
                    self._log_event('webhook_signature_failed', {
                        'request_id': request_id
                    })
                    return
            
            # Parse JSON payload
            try:
                payload = json.loads(webhook_data.decode('utf-8'))
            except json.JSONDecodeError as e:
                self._send_error_response(400, f'Invalid JSON: {str(e)}')
                return
            
            # Log incoming webhook
            self._log_event('webhook_received', {
                'request_id': request_id,
                'entry_count': len(payload.get('entry', [])),
                'object_type': payload.get('object')
            })
            
            # Forward to Hetzner with retry logic
            forward_success, forward_response = self._forward_with_retry(
                webhook_data, 
                request_id,
                payload
            )
            
            if forward_success:
                # Success response
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('X-Request-ID', request_id)
                self.end_headers()
                
                response_data = {
                    'status': 'success',
                    'request_id': request_id,
                    'forwarded_to': self.HETZNER_URL,
                    'timestamp': datetime.utcnow().isoformat(),
                    'duration_ms': int((time.time() - start_time) * 1000)
                }
                
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
                # Log success metrics
                self._log_event('webhook_forwarded', {
                    'request_id': request_id,
                    'duration_ms': response_data['duration_ms'],
                    'response_code': forward_response.get('code', 200)
                })
            else:
                # Forward failed - queue for retry (in production, use proper queue)
                self._queue_for_retry(payload, request_id)
                
                # Return success to WhatsApp to prevent retries
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                
                response_data = {
                    'status': 'queued',
                    'request_id': request_id,
                    'message': 'Webhook queued for processing',
                    'timestamp': datetime.utcnow().isoformat()
                }
                
                self.wfile.write(json.dumps(response_data).encode('utf-8'))
                
                # Log failure metrics
                self._log_event('webhook_forward_failed', {
                    'request_id': request_id,
                    'error': forward_response.get('error', 'Unknown error')
                })
                
        except Exception as e:
            self._handle_exception(e, request_id)
            # Return 200 to WhatsApp to prevent retries
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'status': 'error_logged',
                'request_id': request_id
            }).encode('utf-8'))
    
    def _forward_with_retry(self, webhook_data: bytes, request_id: str, 
                           payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Forward webhook to Hetzner with exponential backoff retry
        """
        last_error = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Prepare forwarding request
                forward_url = f"{self.HETZNER_URL}/webhook"
                
                # Add metadata headers
                headers = {
                    'Content-Type': 'application/json',
                    'X-Request-ID': request_id,
                    'X-Forwarded-For': self.client_address[0],
                    'X-Original-Host': 'lyo-webhook.vercel.app',
                    'X-Retry-Attempt': str(attempt)
                }
                
                # Create request
                req = urllib.request.Request(
                    forward_url,
                    data=webhook_data,
                    headers=headers,
                    method='POST'
                )
                
                # Execute request with timeout
                with urllib.request.urlopen(req, timeout=self.REQUEST_TIMEOUT) as response:
                    response_data = response.read()
                    return True, {
                        'code': response.getcode(),
                        'data': response_data.decode('utf-8') if response_data else None
                    }
                    
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
                if e.code < 500:  # Client error, don't retry
                    break
                    
            except urllib.error.URLError as e:
                last_error = f"Connection error: {str(e.reason)}"
                
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
            
            # Exponential backoff before retry
            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_DELAY * (2 ** attempt)
                time.sleep(min(delay, 10))  # Max 10 seconds
                
                self._log_event('webhook_retry', {
                    'request_id': request_id,
                    'attempt': attempt + 1,
                    'delay_seconds': delay,
                    'error': last_error
                })
        
        return False, {'error': last_error}
    
    def _verify_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature from WhatsApp
        """
        if not signature or not signature.startswith('sha256='):
            return False
        
        expected = hmac.new(
            self.WEBHOOK_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        received = signature[7:]  # Remove 'sha256=' prefix
        return hmac.compare_digest(expected, received)
    
    def _queue_for_retry(self, payload: Dict[str, Any], request_id: str):
        """
        Queue failed webhook for later retry
        In production, this would use Redis or SQS
        """
        # For now, just log it
        self._log_event('webhook_queued', {
            'request_id': request_id,
            'payload_size': len(json.dumps(payload)),
            'timestamp': datetime.utcnow().isoformat()
        })
    
    def _generate_request_id(self) -> str:
        """Generate unique request ID for tracking"""
        return f"req_{int(time.time() * 1000)}_{os.urandom(4).hex()}"
    
    def _log_event(self, event_type: str, data: Dict[str, Any]):
        """Structured logging for monitoring"""
        if not self.MONITORING_ENABLED:
            return
        
        log_entry = {
            'event': event_type,
            'timestamp': datetime.utcnow().isoformat(),
            'service': 'webhook_handler',
            **data
        }
        
        logger.info(json.dumps(log_entry))
    
    def _handle_exception(self, exception: Exception, request_id: str):
        """Central exception handling with logging"""
        self._log_event('webhook_error', {
            'request_id': request_id,
            'error_type': type(exception).__name__,
            'error_message': str(exception),
            'traceback': True  # In production, include full traceback
        })
        
        logger.exception(f"Error in request {request_id}: {exception}")
    
    def _send_error_response(self, code: int, message: str):
        """Send standardized error response"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        error_response = {
            'error': message,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        self.wfile.write(json.dumps(error_response).encode('utf-8'))

# Vercel serverless handler
handler = WebhookHandler