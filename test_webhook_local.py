#!/usr/bin/env python3
"""
Local testing script for WhatsApp webhook before Vercel deployment
Run this to validate webhook functionality
"""
import json
import urllib.request
import urllib.parse
import urllib.error
import time
import sys
from datetime import datetime

class WebhookTester:
    """Test suite for WhatsApp webhook"""
    
    def __init__(self, webhook_url="http://localhost:3000"):
        self.webhook_url = webhook_url
        self.verify_token = "lyo_verify_2024"
        self.tests_passed = 0
        self.tests_failed = 0
    
    def run_all_tests(self):
        """Run complete test suite"""
        print("=" * 60)
        print("WhatsApp Webhook Test Suite")
        print("=" * 60)
        print(f"Testing webhook at: {self.webhook_url}")
        print(f"Timestamp: {datetime.utcnow().isoformat()}")
        print("-" * 60)
        
        # Run tests
        self.test_health_check()
        self.test_webhook_verification()
        self.test_invalid_verification()
        self.test_message_forwarding()
        self.test_empty_payload()
        self.test_large_payload()
        self.test_metrics_endpoint()
        
        # Print summary
        print("-" * 60)
        print("Test Summary:")
        print(f"✅ Passed: {self.tests_passed}")
        print(f"❌ Failed: {self.tests_failed}")
        print(f"Total: {self.tests_passed + self.tests_failed}")
        
        if self.tests_failed == 0:
            print("\n🎉 All tests passed! Webhook is ready for deployment.")
            return True
        else:
            print("\n⚠️ Some tests failed. Please fix issues before deployment.")
            return False
    
    def test_health_check(self):
        """Test health check endpoint"""
        print("\n📋 Test: Health Check Endpoint")
        try:
            url = f"{self.webhook_url}/health"
            req = urllib.request.Request(url)
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if response.getcode() == 200 and data.get('status') in ['healthy', 'degraded']:
                    self._test_passed(f"Health check returned: {data.get('status')}")
                else:
                    self._test_failed("Unexpected health check response")
                    
        except Exception as e:
            self._test_failed(f"Health check failed: {str(e)}")
    
    def test_webhook_verification(self):
        """Test WhatsApp webhook verification"""
        print("\n📋 Test: WhatsApp Webhook Verification")
        try:
            challenge = "test_challenge_123"
            params = urllib.parse.urlencode({
                'hub.mode': 'subscribe',
                'hub.verify_token': self.verify_token,
                'hub.challenge': challenge
            })
            
            url = f"{self.webhook_url}/webhook?{params}"
            req = urllib.request.Request(url)
            
            with urllib.request.urlopen(req, timeout=5) as response:
                result = response.read().decode('utf-8')
                
                if response.getcode() == 200 and result == challenge:
                    self._test_passed(f"Verification successful, challenge returned: {result}")
                else:
                    self._test_failed(f"Verification failed, got: {result}")
                    
        except Exception as e:
            self._test_failed(f"Verification request failed: {str(e)}")
    
    def test_invalid_verification(self):
        """Test webhook verification with invalid token"""
        print("\n📋 Test: Invalid Token Rejection")
        try:
            params = urllib.parse.urlencode({
                'hub.mode': 'subscribe',
                'hub.verify_token': 'invalid_token',
                'hub.challenge': 'test'
            })
            
            url = f"{self.webhook_url}/webhook?{params}"
            req = urllib.request.Request(url)
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.getcode() != 403:
                        self._test_failed("Should reject invalid token with 403")
                    else:
                        self._test_failed("Expected 403 but got 200")
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    self._test_passed("Correctly rejected invalid token")
                else:
                    self._test_failed(f"Wrong error code: {e.code}")
                    
        except Exception as e:
            self._test_failed(f"Invalid token test failed: {str(e)}")
    
    def test_message_forwarding(self):
        """Test message forwarding to Hetzner"""
        print("\n📋 Test: Message Forwarding")
        try:
            # Sample WhatsApp webhook payload
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": "123456789",
                    "changes": [{
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [{
                                "from": "393123456789",
                                "id": "msg_123",
                                "timestamp": str(int(time.time())),
                                "text": {
                                    "body": "Test message from webhook tester"
                                },
                                "type": "text"
                            }]
                        },
                        "field": "messages"
                    }]
                }]
            }
            
            url = f"{self.webhook_url}/webhook"
            data = json.dumps(payload).encode('utf-8')
            
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if response.getcode() == 200:
                    status = result.get('status')
                    if status in ['success', 'queued', 'error_logged']:
                        self._test_passed(f"Message forwarding returned: {status}")
                    else:
                        self._test_failed(f"Unexpected status: {status}")
                else:
                    self._test_failed(f"Wrong status code: {response.getcode()}")
                    
        except Exception as e:
            self._test_failed(f"Message forwarding failed: {str(e)}")
    
    def test_empty_payload(self):
        """Test webhook with empty payload"""
        print("\n📋 Test: Empty Payload Handling")
        try:
            url = f"{self.webhook_url}/webhook"
            req = urllib.request.Request(
                url,
                data=b'',
                headers={'Content-Type': 'application/json'}
            )
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    # Should handle gracefully
                    if response.getcode() in [200, 400]:
                        self._test_passed("Empty payload handled gracefully")
                    else:
                        self._test_failed(f"Unexpected response: {response.getcode()}")
            except urllib.error.HTTPError as e:
                if e.code == 400:
                    self._test_passed("Correctly rejected empty payload")
                else:
                    self._test_failed(f"Wrong error code: {e.code}")
                    
        except Exception as e:
            self._test_failed(f"Empty payload test failed: {str(e)}")
    
    def test_large_payload(self):
        """Test webhook with large payload"""
        print("\n📋 Test: Large Payload Handling")
        try:
            # Create a large but valid payload
            large_text = "x" * 10000  # 10KB message
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "changes": [{
                        "value": {
                            "messages": [{
                                "from": "393123456789",
                                "text": {"body": large_text},
                                "type": "text"
                            }]
                        }
                    }]
                }]
            }
            
            url = f"{self.webhook_url}/webhook"
            data = json.dumps(payload).encode('utf-8')
            
            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.getcode() == 200:
                    self._test_passed("Large payload processed successfully")
                else:
                    self._test_failed(f"Failed with code: {response.getcode()}")
                    
        except Exception as e:
            self._test_failed(f"Large payload test failed: {str(e)}")
    
    def test_metrics_endpoint(self):
        """Test metrics endpoint"""
        print("\n📋 Test: Metrics Endpoint")
        try:
            url = f"{self.webhook_url}/metrics"
            req = urllib.request.Request(url)
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if response.getcode() == 200 and 'metrics' in data:
                    self._test_passed("Metrics endpoint working")
                else:
                    self._test_failed("Invalid metrics response")
                    
        except Exception as e:
            self._test_failed(f"Metrics endpoint failed: {str(e)}")
    
    def _test_passed(self, message):
        """Mark test as passed"""
        print(f"  ✅ PASS: {message}")
        self.tests_passed += 1
    
    def _test_failed(self, message):
        """Mark test as failed"""
        print(f"  ❌ FAIL: {message}")
        self.tests_failed += 1

def main():
    """Main test runner"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Test WhatsApp webhook locally')
    parser.add_argument(
        '--url',
        default='http://localhost:3000',
        help='Webhook URL to test (default: http://localhost:3000)'
    )
    
    args = parser.parse_args()
    
    print(f"Starting webhook tests against: {args.url}")
    print("Make sure your webhook server is running!")
    print()
    
    tester = WebhookTester(args.url)
    success = tester.run_all_tests()
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()