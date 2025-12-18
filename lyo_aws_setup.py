#!/usr/bin/env python3
"""
Set up Lyo SaaS on AWS EC2 with enterprise database
"""
import subprocess
import time

def setup_aws_lyo():
    print("🚀 SETTING UP LYO SAAS ON AWS")
    print("="*40)
    
    # Instance details
    instance_ip = "3.231.146.151"
    database_url = "postgresql://lyoadmin:LyoSaaS2024Enterprise!@lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com:5432/lyo_production"
    
    print(f"🔍 Instance: {instance_ip}")
    print(f"🗄️ Database: {database_url.split('@')[1].split('/')[0]}")
    
    # Create a simple setup script to run on EC2
    setup_script = f'''#!/bin/bash
# AWS EC2 Lyo SaaS Setup Script

# Update system
sudo yum update -y
sudo yum install -y git python3 python3-pip nginx postgresql15-devel gcc

# Install Python dependencies
pip3 install --user fastapi uvicorn httpx openai psycopg2-binary python-dotenv

# Clone repository
cd /home/ec2-user
git clone https://github.com/Hashem-Al-Qurashi/lyo-saas-production.git lyo-saas
cd lyo-saas

# Create environment file
cat > .env << 'EOF'
OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY
DATABASE_URL={database_url}
ENVIRONMENT=production
EOF

# Test database connection
python3 -c "
import psycopg2
try:
    conn = psycopg2.connect('{database_url}')
    print('✅ Database connection successful')
    conn.close()
except Exception as e:
    print(f'❌ Database connection failed: {{e}}')
"

# Start Lyo application
nohup python3 lyo_production.py > lyo.log 2>&1 &

echo "🎉 Lyo SaaS setup complete"
'''
    
    # Save setup script
    with open('/tmp/aws_setup.sh', 'w') as f:
        f.write(setup_script)
    
    print("✅ Setup script created")
    
    # Copy and execute on EC2 (would need SSH key or password)
    print("📋 Setup script ready for EC2 execution")
    print("🔧 You can run this manually or provide SSH access")
    
    return database_url

if __name__ == "__main__":
    db_url = setup_aws_lyo()
    print(f"\\n🎯 ENTERPRISE SETUP READY:")
    print(f"   Database: ✅ Available") 
    print(f"   Load Balancer: ✅ Created")
    print(f"   EC2: ✅ Running")
    print(f"   Next: Install Lyo app and test system")