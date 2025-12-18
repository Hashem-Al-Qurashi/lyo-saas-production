#!/usr/bin/env python3
"""
Install Lyo SaaS on AWS EC2 with persistent PostgreSQL
"""
import paramiko
import time

def install_lyo_on_ec2():
    print("🚀 INSTALLING LYO SAAS ON ENTERPRISE EC2")
    print("="*50)
    
    # EC2 connection details
    hostname = "3.231.146.151"
    username = "ec2-user"  # Amazon Linux default user
    
    # Database connection string
    db_url = "postgresql://lyoadmin:LyoSaaS2024Enterprise!@lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com:5432/lyo_production"
    
    try:
        # Connect to EC2 (might need to wait for SSH to be ready)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        print("🔌 Connecting to EC2 instance...")
        for attempt in range(5):
            try:
                ssh.connect(hostname, username=username, look_for_keys=True, timeout=30)
                print("✅ Connected to EC2 instance")
                break
            except Exception as e:
                print(f"   Attempt {attempt + 1}/5 failed: {e}")
                time.sleep(10)
        else:
            print("❌ Could not connect to EC2")
            return False
        
        # Install system dependencies
        print("📦 Installing system dependencies...")
        commands = [
            "sudo yum update -y",
            "sudo yum install -y git python3 python3-pip nginx postgresql15",
            "sudo pip3 install --upgrade pip"
        ]
        
        for cmd in commands:
            stdin, stdout, stderr = ssh.exec_command(cmd)
            stdout.read()  # Wait for completion
            print(f"   ✅ {cmd}")
        
        # Clone Lyo repository
        print("📁 Cloning Lyo SaaS repository...")
        stdin, stdout, stderr = ssh.exec_command("git clone https://github.com/Hashem-Al-Qurashi/lyo-saas-production.git /home/ec2-user/lyo-saas")
        stdout.read()
        print("   ✅ Repository cloned")
        
        # Install Python dependencies
        print("🐍 Installing Python dependencies...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/ec2-user/lyo-saas && pip3 install --user -r requirements.txt")
        install_output = stdout.read().decode()
        print("   ✅ Dependencies installed")
        
        # Create environment file with database connection
        print("🔧 Creating environment configuration...")
        env_content = f'''OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY
WHATSAPP_TOKEN=EAANnWHIj0VkBQOseol46TjgFZADH5GTj9cY45WSZBEGd4qmEOCPFyO4DmQh6bM34k0moGZCgJO1XanNpgKaagPbtSAw8Lc8yq
WHATSAPP_PHONE_ID=961636900357709
WEBHOOK_VERIFY_TOKEN=lyo_verify_2024
DATABASE_URL={db_url}
ENVIRONMENT=production
DEBUG=false'''
        
        stdin, stdout, stderr = ssh.exec_command(f'cd /home/ec2-user/lyo-saas && echo "{env_content}" > .env')
        print("   ✅ Environment configured")
        
        # Test database connection
        print("🔍 Testing database connection...")
        test_db_cmd = f'python3 -c "import psycopg2; conn = psycopg2.connect(\\"{db_url}\\"); print(\\"✅ Database connected\\"); conn.close()"'
        stdin, stdout, stderr = ssh.exec_command(f"cd /home/ec2-user/lyo-saas && pip3 install --user psycopg2-binary && {test_db_cmd}")
        db_test = stdout.read().decode()
        db_error = stderr.read().decode()
        print(f"   Database test: {db_test if db_test else db_error}")
        
        # Start Lyo application
        print("🚀 Starting Lyo SaaS application...")
        stdin, stdout, stderr = ssh.exec_command("cd /home/ec2-user/lyo-saas && nohup python3 lyo_production.py > lyo.log 2>&1 &")
        print("   ✅ Lyo application started")
        
        # Test application
        time.sleep(10)
        stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8005/health")
        health_test = stdout.read().decode()
        print(f"   🏥 Health test: {health_test}")
        
        ssh.close()
        return True
        
    except Exception as e:
        print(f"❌ Installation failed: {e}")
        return False

if __name__ == "__main__":
    install_lyo_on_ec2()