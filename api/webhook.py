"""
Vercel-compatible WhatsApp Webhook
Simple function interface for serverless deployment
"""
import json
import urllib.request
import urllib.parse
from urllib.error import HTTPError, URLError

def handler(request):
    """
    Vercel serverless function handler
    Compatible with Vercel Python runtime
    """
    # Get request method
    method = request.method
    
    if method == "GET":
        # Handle WhatsApp webhook verification
        return handle_verification(request)
    elif method == "POST":
        # Handle webhook message forwarding
        return handle_webhook(request)
    else:
        return create_response("Method not allowed", 405)

def handle_verification(request):
    """Handle WhatsApp webhook verification challenge"""
    try:
        # Parse query parameters
        query_params = dict(request.args) if hasattr(request, 'args') else {}
        
        # Extract from URL if args not available
        if not query_params and hasattr(request, 'url'):
            parsed = urllib.parse.urlparse(str(request.url))
            query_params = urllib.parse.parse_qs(parsed.query)
            # Convert list values to single values
            query_params = {k: v[0] if v else None for k, v in query_params.items()}
        
        mode = query_params.get('hub.mode')
        token = query_params.get('hub.verify_token')
        challenge = query_params.get('hub.challenge')
        
        # Verify webhook subscription
        if mode == 'subscribe' and token == 'lyo_verify_2024':
            # Return challenge for successful verification
            return create_response(challenge or 'OK', 200, 'text/plain')
        else:
            return create_response('Forbidden', 403, 'text/plain')
            
    except Exception as e:
        return create_response(f'Verification error: {str(e)}', 500)

def handle_webhook(request):
    """Handle WhatsApp webhook POST requests"""
    try:
        # Get request body
        if hasattr(request, 'get_json'):
            # Vercel request object
            webhook_data = request.get_json()
            webhook_json = json.dumps(webhook_data).encode('utf-8')
        elif hasattr(request, 'body'):
            # Raw body
            webhook_json = request.body
        else:
            return create_response('Cannot read request body', 400)
        
        # Forward to Hetzner server
        success = forward_to_hetzner(webhook_json)
        
        if success:
            return create_response({"status": "forwarded"}, 200)
        else:
            # Still return 200 to WhatsApp to prevent retries
            return create_response({"status": "queued"}, 200)
            
    except Exception as e:
        # Return 200 to prevent WhatsApp retries
        return create_response({"error": str(e), "status": "logged"}, 200)

def forward_to_hetzner(webhook_data):
    """Forward webhook to Hetzner production server"""
    try:
        hetzner_url = "http://135.181.249.116:8000/webhook"
        
        req = urllib.request.Request(
            hetzner_url,
            data=webhook_data,
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.getcode() == 200
            
    except (HTTPError, URLError, Exception):
        return False

def create_response(data, status_code=200, content_type='application/json'):
    """Create Vercel-compatible response"""
    if content_type == 'text/plain':
        body = str(data)
    else:
        body = json.dumps(data) if not isinstance(data, str) else data
    
    # Return simple response that Vercel can handle
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': content_type,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        'body': body
    }