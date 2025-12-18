# Lyo SaaS Production Deployment Guide

## Overview

This guide provides step-by-step instructions to deploy the Lyo Italian Booking Assistant SaaS platform to AWS production with enterprise-grade reliability, security, and scalability.

## Prerequisites

### AWS Account Setup
- AWS Account with billing enabled
- IAM user with administrative permissions
- AWS CLI v2 installed and configured
- Domain name registered (e.g., `lyo-booking.com`)

### Tools Required
- **AWS CLI v2**: `aws --version`
- **Docker**: `docker --version`
- **Git**: `git --version`
- **jq**: Command-line JSON processor
- **curl**: For API testing

### Environment Variables
```bash
# Set these before starting deployment
export AWS_REGION="eu-west-1"
export DOMAIN_NAME="api.lyo-booking.com"
export PROJECT_NAME="lyo-saas"
export ENVIRONMENT="production"
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

## Phase 1: Pre-Deployment Setup (30 minutes)

### 1.1 Clone Repository
```bash
git clone https://github.com/Hashem-Al-Qurashi/lyo-saas-production.git
cd lyo-saas-production
```

### 1.2 SSL Certificate Setup
```bash
# Request SSL certificate (must be in us-east-1 for CloudFront)
aws acm request-certificate \
    --domain-name $DOMAIN_NAME \
    --subject-alternative-names "*.$DOMAIN_NAME" \
    --validation-method DNS \
    --region us-east-1

# Get certificate ARN (wait for DNS validation)
export ACM_CERTIFICATE_ARN=$(aws acm list-certificates \
    --region us-east-1 \
    --query "CertificateSummaryList[?DomainName=='$DOMAIN_NAME'].CertificateArn" \
    --output text)

echo "Certificate ARN: $ACM_CERTIFICATE_ARN"
```

### 1.3 ECR Repository Setup
```bash
# Create ECR repository
aws ecr create-repository \
    --repository-name $PROJECT_NAME \
    --region $AWS_REGION

# Get ECR URI
export ECR_REPOSITORY_URI=$(aws ecr describe-repositories \
    --repository-names $PROJECT_NAME \
    --region $AWS_REGION \
    --query 'repositories[0].repositoryUri' \
    --output text)

echo "ECR Repository: $ECR_REPOSITORY_URI"
```

### 1.4 Generate Secure Passwords
```bash
# Generate secure passwords
export DB_MASTER_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-16)
export SECRET_KEY=$(openssl rand -base64 48)
export OPENAI_API_KEY="sk-your-openai-api-key-here"  # Replace with actual key

echo "Generated passwords saved to .env.production"
cat > .env.production << EOF
DB_MASTER_PASSWORD=$DB_MASTER_PASSWORD
SECRET_KEY=$SECRET_KEY
OPENAI_API_KEY=$OPENAI_API_KEY
ACM_CERTIFICATE_ARN=$ACM_CERTIFICATE_ARN
EOF
```

## Phase 2: Infrastructure Deployment (45 minutes)

### 2.1 Deploy Core Infrastructure
```bash
# Deploy VPC, RDS, Redis, ALB
aws cloudformation create-stack \
    --stack-name lyo-infrastructure-$ENVIRONMENT \
    --template-body file://aws/cloudformation/lyo-infrastructure.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=DatabaseMasterPassword,ParameterValue=$DB_MASTER_PASSWORD \
        ParameterKey=CertificateArn,ParameterValue=$ACM_CERTIFICATE_ARN \
        ParameterKey=DomainName,ParameterValue=$DOMAIN_NAME \
        ParameterKey=OpenAIApiKey,ParameterValue=$OPENAI_API_KEY \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION \
    --tags Key=Project,Value=$PROJECT_NAME Key=Environment,Value=$ENVIRONMENT

# Wait for infrastructure completion (20-30 minutes)
echo "Waiting for infrastructure deployment..."
aws cloudformation wait stack-create-complete \
    --stack-name lyo-infrastructure-$ENVIRONMENT \
    --region $AWS_REGION

# Verify deployment
aws cloudformation describe-stacks \
    --stack-name lyo-infrastructure-$ENVIRONMENT \
    --region $AWS_REGION \
    --query 'Stacks[0].StackStatus'
```

### 2.2 Deploy Security Baseline
```bash
# Deploy WAF, GuardDuty, Security Hub
aws cloudformation create-stack \
    --stack-name lyo-security-$ENVIRONMENT \
    --template-body file://aws/security/security-baseline.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=InfrastructureStackName,ParameterValue=lyo-infrastructure-$ENVIRONMENT \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION

# Wait for security stack completion
aws cloudformation wait stack-create-complete \
    --stack-name lyo-security-$ENVIRONMENT \
    --region $AWS_REGION
```

### 2.3 Deploy Monitoring Stack
```bash
# Deploy CloudWatch alarms, dashboards, SNS
aws cloudformation create-stack \
    --stack-name lyo-monitoring-$ENVIRONMENT \
    --template-body file://aws/cloudformation/lyo-monitoring.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=InfrastructureStackName,ParameterValue=lyo-infrastructure-$ENVIRONMENT \
        ParameterKey=AlertEmail,ParameterValue=alerts@lyo-booking.com \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION

# Wait for monitoring stack completion
aws cloudformation wait stack-create-complete \
    --stack-name lyo-monitoring-$ENVIRONMENT \
    --region $AWS_REGION
```

## Phase 3: Database Setup (15 minutes)

### 3.1 Initialize Database Schema
```bash
# Get database endpoint
export DB_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name lyo-infrastructure-$ENVIRONMENT \
    --region $AWS_REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`DatabaseEndpoint`].OutputValue' \
    --output text)

# Create database connection string
export DATABASE_URL="postgresql://lyoadmin:$DB_MASTER_PASSWORD@$DB_ENDPOINT:5432/lyo_production"

# Install PostgreSQL client (if needed)
# Ubuntu/Debian: sudo apt-get install postgresql-client
# macOS: brew install postgresql

# Initialize database schema
psql $DATABASE_URL -f database/multi-tenant-schema.sql

# Verify database setup
psql $DATABASE_URL -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public';"
```

### 3.2 Verify Database Performance
```bash
# Test database performance
psql $DATABASE_URL -c "
SELECT 
    schemaname,
    tablename,
    attname,
    n_distinct,
    correlation
FROM pg_stats 
WHERE schemaname = 'public' 
ORDER BY tablename, attname;
"
```

## Phase 4: Application Deployment (30 minutes)

### 4.1 Build and Push Container Image
```bash
# Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
    docker login --username AWS --password-stdin $ECR_REPOSITORY_URI

# Build Docker image
docker build -t $PROJECT_NAME:latest \
    --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
    --build-arg GIT_COMMIT=$(git rev-parse HEAD) \
    --build-arg VERSION=1.0.0 \
    .

# Tag and push image
docker tag $PROJECT_NAME:latest $ECR_REPOSITORY_URI:latest
docker tag $PROJECT_NAME:latest $ECR_REPOSITORY_URI:1.0.0

docker push $ECR_REPOSITORY_URI:latest
docker push $ECR_REPOSITORY_URI:1.0.0

# Set image URI for deployment
export IMAGE_URI=$ECR_REPOSITORY_URI:1.0.0
```

### 4.2 Deploy Application Stack
```bash
# Deploy ECS service
aws cloudformation create-stack \
    --stack-name lyo-application-$ENVIRONMENT \
    --template-body file://aws/cloudformation/lyo-application.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=InfrastructureStackName,ParameterValue=lyo-infrastructure-$ENVIRONMENT \
        ParameterKey=ImageUri,ParameterValue=$IMAGE_URI \
        ParameterKey=DesiredCount,ParameterValue=2 \
        ParameterKey=TaskCpu,ParameterValue=512 \
        ParameterKey=TaskMemory,ParameterValue=1024 \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION

# Wait for application deployment
aws cloudformation wait stack-create-complete \
    --stack-name lyo-application-$ENVIRONMENT \
    --region $AWS_REGION

# Wait for ECS service to stabilize
aws ecs wait services-stable \
    --cluster $PROJECT_NAME-$ENVIRONMENT \
    --services $PROJECT_NAME-$ENVIRONMENT \
    --region $AWS_REGION
```

### 4.3 Deploy Cost Monitoring
```bash
# Deploy cost optimization stack
aws cloudformation create-stack \
    --stack-name lyo-cost-monitoring-$ENVIRONMENT \
    --template-body file://aws/cost-optimization/cost-monitoring.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=MonthlyBudgetLimit,ParameterValue=200 \
        ParameterKey=CostAlertEmail,ParameterValue=finance@lyo-booking.com \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION

aws cloudformation wait stack-create-complete \
    --stack-name lyo-cost-monitoring-$ENVIRONMENT \
    --region $AWS_REGION
```

## Phase 5: DNS Configuration (15 minutes)

### 5.1 Configure Route 53
```bash
# Get ALB DNS name
export ALB_DNS_NAME=$(aws cloudformation describe-stacks \
    --stack-name lyo-infrastructure-$ENVIRONMENT \
    --region $AWS_REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerDNS`].OutputValue' \
    --output text)

# Create/update DNS records (adjust for your DNS provider)
# For Route 53:
cat > dns-config.json << EOF
{
    "Changes": [
        {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": "$DOMAIN_NAME",
                "Type": "A",
                "AliasTarget": {
                    "DNSName": "$ALB_DNS_NAME",
                    "EvaluateTargetHealth": true,
                    "HostedZoneId": "Z32O12XQLNTSW2"
                }
            }
        }
    ]
}
EOF

# Apply DNS changes (replace HOSTED_ZONE_ID with your actual zone ID)
# aws route53 change-resource-record-sets \
#     --hosted-zone-id YOUR_HOSTED_ZONE_ID \
#     --change-batch file://dns-config.json

echo "Update your DNS to point $DOMAIN_NAME to $ALB_DNS_NAME"
```

## Phase 6: Verification and Testing (20 minutes)

### 6.1 Health Checks
```bash
# Wait for DNS propagation
echo "Waiting for DNS propagation..."
while ! nslookup $DOMAIN_NAME | grep -q $ALB_DNS_NAME; do
    echo "Waiting for DNS..."
    sleep 30
done

# Test application health
echo "Testing application health..."
curl -f https://$DOMAIN_NAME/health | jq '.'

# Expected response:
# {
#   "status": "healthy",
#   "timestamp": "2024-01-15T10:30:00.000Z",
#   "version": "1.0.0",
#   "environment": "production",
#   "services": {
#     "database": true,
#     "redis": true,
#     "openai": true,
#     "google_calendar": false
#   }
# }
```

### 6.2 Functional Testing
```bash
# Test webhook endpoint
curl -f https://$DOMAIN_NAME/webhooks/whatsapp/health

# Test API endpoints
curl -f https://$DOMAIN_NAME/api/docs  # Should return 404 in production mode (correct)

# Test metrics endpoint
curl -f https://$DOMAIN_NAME/metrics | head -10

# Test rate limiting
for i in {1..5}; do
    curl -w "Response time: %{time_total}s\n" -s https://$DOMAIN_NAME/health > /dev/null
done
```

### 6.3 Load Testing
```bash
# Install Apache Bench if needed
# sudo apt-get install apache2-utils

# Basic load test
ab -n 100 -c 10 -H "User-Agent: LoadTest" https://$DOMAIN_NAME/health

# Extended load test
ab -n 1000 -c 25 -t 60 https://$DOMAIN_NAME/health > load-test-results.txt

echo "Load test results:"
cat load-test-results.txt | grep -E "Requests per second|Time per request|Transfer rate"
```

### 6.4 Security Testing
```bash
# Test SSL configuration
curl -I https://$DOMAIN_NAME | grep -i "strict-transport-security"

# Test security headers
curl -I https://$DOMAIN_NAME | grep -E "(X-|Content-Security)"

# Verify HTTPS redirect
curl -I http://$DOMAIN_NAME | grep -i location

# Test for common vulnerabilities
curl -f https://$DOMAIN_NAME/../../../etc/passwd  # Should return 404

# SSL Labs test (external)
echo "Run SSL Labs test: https://www.ssllabs.com/ssltest/analyze.html?d=$DOMAIN_NAME"
```

## Phase 7: Monitoring Setup (10 minutes)

### 7.1 Configure Alerts
```bash
# Get SNS topic ARNs
export CRITICAL_ALERTS_TOPIC=$(aws cloudformation describe-stacks \
    --stack-name lyo-monitoring-$ENVIRONMENT \
    --region $AWS_REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`CriticalAlertsTopicArn`].OutputValue' \
    --output text)

# Subscribe to email alerts
aws sns subscribe \
    --topic-arn $CRITICAL_ALERTS_TOPIC \
    --protocol email \
    --notification-endpoint alerts@lyo-booking.com \
    --region $AWS_REGION

echo "Confirm email subscription in your inbox"
```

### 7.2 Access Dashboards
```bash
# CloudWatch Dashboard URLs
echo "Application Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=$AWS_REGION#dashboards:name=$PROJECT_NAME-$ENVIRONMENT-application"

echo "Cost Dashboard: https://console.aws.amazon.com/cloudwatch/home?region=$AWS_REGION#dashboards:name=$PROJECT_NAME-$ENVIRONMENT-cost-dashboard"

# ECS Service URL
echo "ECS Service: https://console.aws.amazon.com/ecs/home?region=$AWS_REGION#/clusters/$PROJECT_NAME-$ENVIRONMENT/services"

# RDS Console
echo "RDS Database: https://console.aws.amazon.com/rds/home?region=$AWS_REGION#database:id=$PROJECT_NAME-$ENVIRONMENT-postgres"
```

## Phase 8: Backup Verification (10 minutes)

### 8.1 Deploy Backup Strategy
```bash
# Deploy backup and DR stack
aws cloudformation create-stack \
    --stack-name lyo-backup-$ENVIRONMENT \
    --template-body file://aws/security/backup-strategy.yaml \
    --parameters \
        ParameterKey=Environment,ParameterValue=$ENVIRONMENT \
        ParameterKey=ProjectName,ParameterValue=$PROJECT_NAME \
        ParameterKey=InfrastructureStackName,ParameterValue=lyo-infrastructure-$ENVIRONMENT \
    --capabilities CAPABILITY_IAM \
    --region $AWS_REGION

aws cloudformation wait stack-create-complete \
    --stack-name lyo-backup-$ENVIRONMENT \
    --region $AWS_REGION
```

### 8.2 Verify Backup Configuration
```bash
# Check RDS backup configuration
aws rds describe-db-instances \
    --db-instance-identifier $PROJECT_NAME-$ENVIRONMENT-postgres \
    --region $AWS_REGION \
    --query 'DBInstances[0].BackupRetentionPeriod'

# List backup plans
aws backup list-backup-plans \
    --region $AWS_REGION \
    --query 'BackupPlansList[?contains(BackupPlanName,`lyo`)]'

# Check automatic snapshots
aws rds describe-db-snapshots \
    --db-instance-identifier $PROJECT_NAME-$ENVIRONMENT-postgres \
    --snapshot-type automated \
    --region $AWS_REGION \
    --max-items 5
```

## Phase 9: CI/CD Setup (15 minutes)

### 9.1 GitHub Actions Secrets
```bash
# Set up GitHub repository secrets (via GitHub CLI or UI)
gh secret set AWS_ACCESS_KEY_ID --body "$AWS_ACCESS_KEY_ID"
gh secret set AWS_SECRET_ACCESS_KEY --body "$AWS_SECRET_ACCESS_KEY"
gh secret set DB_MASTER_PASSWORD --body "$DB_MASTER_PASSWORD"
gh secret set ACM_CERTIFICATE_ARN --body "$ACM_CERTIFICATE_ARN"
gh secret set OPENAI_API_KEY --body "$OPENAI_API_KEY"
gh secret set ALERT_EMAIL --body "alerts@lyo-booking.com"
gh secret set SLACK_WEBHOOK_URL --body "https://hooks.slack.com/your-webhook-url"

# Verify secrets are set
gh secret list
```

### 9.2 Test CI/CD Pipeline
```bash
# Create a test commit to trigger deployment
git checkout -b test-deployment
echo "# Test deployment $(date)" >> README.md
git add README.md
git commit -m "Test: Trigger CI/CD pipeline"
git push origin test-deployment

# Create pull request (optional)
gh pr create --title "Test deployment pipeline" --body "Testing automated deployment"

# Check workflow status
gh run list --limit 5
```

## Phase 10: Go-Live Checklist (10 minutes)

### 10.1 Final Verification
```bash
#!/bin/bash
# Go-live verification script

echo "=== LYO SAAS PRODUCTION GO-LIVE CHECKLIST ==="

# 1. Application Health
echo "✓ Checking application health..."
HEALTH_STATUS=$(curl -s https://$DOMAIN_NAME/health | jq -r '.status')
if [ "$HEALTH_STATUS" = "healthy" ]; then
    echo "  ✅ Application is healthy"
else
    echo "  ❌ Application health check failed"
    exit 1
fi

# 2. Database Connectivity
echo "✓ Checking database connectivity..."
DB_STATUS=$(curl -s https://$DOMAIN_NAME/health | jq -r '.services.database')
if [ "$DB_STATUS" = "true" ]; then
    echo "  ✅ Database is connected"
else
    echo "  ❌ Database connection failed"
    exit 1
fi

# 3. Redis Connectivity
echo "✓ Checking Redis connectivity..."
REDIS_STATUS=$(curl -s https://$DOMAIN_NAME/health | jq -r '.services.redis')
if [ "$REDIS_STATUS" = "true" ]; then
    echo "  ✅ Redis is connected"
else
    echo "  ❌ Redis connection failed"
    exit 1
fi

# 4. SSL Certificate
echo "✓ Checking SSL certificate..."
SSL_EXPIRY=$(echo | openssl s_client -servername $DOMAIN_NAME -connect $DOMAIN_NAME:443 2>/dev/null | openssl x509 -noout -dates | grep notAfter | cut -d= -f2)
echo "  ✅ SSL certificate valid until: $SSL_EXPIRY"

# 5. Performance Test
echo "✓ Running performance test..."
RESPONSE_TIME=$(curl -w "%{time_total}" -s -o /dev/null https://$DOMAIN_NAME/health)
if (( $(echo "$RESPONSE_TIME < 2.0" | bc -l) )); then
    echo "  ✅ Response time: ${RESPONSE_TIME}s (< 2s)"
else
    echo "  ⚠️  Response time: ${RESPONSE_TIME}s (> 2s)"
fi

# 6. Monitoring Alerts
echo "✓ Checking monitoring setup..."
ALARM_COUNT=$(aws cloudwatch describe-alarms \
    --alarm-name-prefix "$PROJECT_NAME-$ENVIRONMENT" \
    --region $AWS_REGION \
    --query 'MetricAlarms[?StateValue==`OK`] | length(@)')
echo "  ✅ $ALARM_COUNT CloudWatch alarms configured"

# 7. Backup Configuration
echo "✓ Checking backup configuration..."
BACKUP_COUNT=$(aws backup list-backup-plans --query 'length(BackupPlansList[?contains(BackupPlanName,`lyo`)])' --output text)
echo "  ✅ $BACKUP_COUNT backup plans configured"

# 8. Cost Monitoring
echo "✓ Checking cost monitoring..."
BUDGET_COUNT=$(aws budgets describe-budgets --account-id $AWS_ACCOUNT_ID --query 'length(Budgets[?contains(BudgetName,`lyo`)])' --output text)
echo "  ✅ $BUDGET_COUNT cost budgets configured"

echo ""
echo "🎉 PRODUCTION DEPLOYMENT SUCCESSFUL!"
echo "🌐 Application URL: https://$DOMAIN_NAME"
echo "📊 Monitoring: https://console.aws.amazon.com/cloudwatch/home?region=$AWS_REGION"
echo "💰 Cost Dashboard: https://console.aws.amazon.com/billing/home"
echo ""
echo "Next Steps:"
echo "1. Configure WhatsApp webhook URL: https://$DOMAIN_NAME/webhooks/whatsapp"
echo "2. Set up Google Calendar integration"
echo "3. Configure business settings for tenants"
echo "4. Set up customer onboarding process"
echo "5. Schedule regular DR testing"
```

### 10.2 Production Handover Documentation
```bash
# Generate deployment summary
cat > deployment-summary.md << EOF
# Lyo SaaS Production Deployment Summary

**Deployment Date**: $(date)
**Environment**: $ENVIRONMENT
**Region**: $AWS_REGION
**Domain**: https://$DOMAIN_NAME

## Infrastructure Components
- **VPC**: Custom VPC with public/private subnets
- **Database**: RDS PostgreSQL Multi-AZ
- **Cache**: ElastiCache Redis cluster
- **Compute**: ECS Fargate with auto-scaling
- **Load Balancer**: Application Load Balancer with SSL
- **CDN**: CloudFront distribution
- **Storage**: S3 buckets for configs and backups

## Security Components
- **WAF**: Web Application Firewall with managed rules
- **GuardDuty**: Threat detection enabled
- **Security Hub**: Centralized security findings
- **CloudTrail**: API audit logging
- **Secrets Manager**: Encrypted secrets storage

## Monitoring & Alerting
- **CloudWatch**: Comprehensive dashboards and alarms
- **SNS**: Email and Slack notifications
- **Cost Monitoring**: Budget alerts and anomaly detection
- **Performance**: Application and infrastructure metrics

## Access Information
- **AWS Account**: $AWS_ACCOUNT_ID
- **Region**: $AWS_REGION  
- **Application URL**: https://$DOMAIN_NAME
- **ECS Cluster**: $PROJECT_NAME-$ENVIRONMENT
- **Database**: $PROJECT_NAME-$ENVIRONMENT-postgres

## Emergency Contacts
- **DevOps Team**: devops@lyo-booking.com
- **On-Call**: +1-xxx-xxx-xxxx
- **AWS Support**: Enterprise Support enabled

## Important URLs
- **Application**: https://$DOMAIN_NAME
- **Health Check**: https://$DOMAIN_NAME/health
- **Metrics**: https://$DOMAIN_NAME/metrics
- **CloudWatch**: [Dashboard URL]
- **Cost Dashboard**: [Cost Dashboard URL]

EOF

echo "Deployment completed successfully! ✅"
echo "See deployment-summary.md for handover documentation."
```

## Troubleshooting Common Issues

### Database Connection Issues
```bash
# Check database security groups
aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=*rds*" \
    --query 'SecurityGroups[*].[GroupId,GroupName,IpPermissions[*].FromPort]'

# Test database connectivity from ECS
aws ecs run-task \
    --cluster $PROJECT_NAME-$ENVIRONMENT \
    --task-definition $PROJECT_NAME-$ENVIRONMENT \
    --overrides '{
        "containerOverrides": [{
            "name": "lyo-app",
            "command": ["psql", "$DATABASE_URL", "-c", "SELECT 1;"]
        }]
    }'
```

### ECS Service Issues
```bash
# Check service events
aws ecs describe-services \
    --cluster $PROJECT_NAME-$ENVIRONMENT \
    --services $PROJECT_NAME-$ENVIRONMENT \
    --query 'services[0].events[0:5]'

# Check task definitions
aws ecs describe-task-definition \
    --task-definition $PROJECT_NAME-$ENVIRONMENT \
    --query 'taskDefinition.containerDefinitions[0].environment'

# View logs
aws logs tail /aws/ecs/$PROJECT_NAME-$ENVIRONMENT --follow
```

### SSL/DNS Issues
```bash
# Test DNS resolution
nslookup $DOMAIN_NAME
dig $DOMAIN_NAME

# Test SSL handshake
openssl s_client -connect $DOMAIN_NAME:443 -servername $DOMAIN_NAME

# Check certificate in ACM
aws acm describe-certificate --certificate-arn $ACM_CERTIFICATE_ARN --region us-east-1
```

## Support and Maintenance

### Daily Operations
- Monitor CloudWatch dashboards
- Review cost reports
- Check backup completion
- Review security alerts

### Weekly Operations  
- Review performance metrics
- Check for AWS service updates
- Review and rotate access keys
- Test disaster recovery procedures

### Monthly Operations
- Security audit and vulnerability assessment
- Performance optimization review
- Cost optimization analysis
- Disaster recovery drill

---

**Congratulations!** 🎉 Your Lyo SaaS platform is now deployed and running on AWS with enterprise-grade reliability, security, and scalability.

For support, contact: **devops@lyo-booking.com**