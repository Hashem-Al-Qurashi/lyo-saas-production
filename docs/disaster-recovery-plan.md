# Lyo SaaS Disaster Recovery Plan

## Executive Summary

This document outlines the comprehensive disaster recovery (DR) strategy for the Lyo Italian Booking Assistant SaaS platform. Our DR plan ensures business continuity with **RTO ≤ 4 hours** and **RPO ≤ 15 minutes** for production environments.

## Disaster Recovery Objectives

| Metric | Target | Measurement |
|--------|---------|-------------|
| **RTO (Recovery Time Objective)** | ≤ 4 hours | Time to restore service |
| **RPO (Recovery Point Objective)** | ≤ 15 minutes | Acceptable data loss |
| **Availability** | 99.9% | Annual uptime target |
| **MTTR (Mean Time To Recovery)** | ≤ 2 hours | Average incident resolution |

## Disaster Scenarios

### Critical Scenarios (Tier 1 - Immediate Response)
1. **Complete AWS Region Failure** - Primary region (eu-west-1) unavailable
2. **Database Corruption/Failure** - PostgreSQL data loss or corruption  
3. **Application Security Breach** - Unauthorized access or data compromise
4. **Multi-Service Outage** - Multiple AWS services unavailable

### High Impact Scenarios (Tier 2 - 4-hour Response)
1. **ECS Service Failure** - Application containers not starting
2. **Load Balancer Failure** - ALB not routing traffic
3. **Network Connectivity Issues** - VPC/subnet problems
4. **Certificate Expiration** - SSL/TLS certificates expired

### Medium Impact Scenarios (Tier 3 - 24-hour Response)
1. **Redis Cache Failure** - Session/conversation data loss
2. **Monitoring System Down** - CloudWatch/alerting offline
3. **CI/CD Pipeline Failure** - Unable to deploy updates
4. **Backup System Failure** - Backup jobs failing

## Recovery Architecture

### Primary Region: eu-west-1 (Ireland)
- **Production Environment**: Full deployment
- **RDS PostgreSQL**: Multi-AZ with automated backups
- **Redis**: Multi-AZ replication group
- **ECS Fargate**: Multi-AZ deployment
- **S3**: Cross-region replication enabled

### Disaster Recovery Region: us-west-2 (Oregon)
- **Standby Environment**: Infrastructure pre-provisioned
- **RDS PostgreSQL**: Cross-region read replica
- **S3**: Replicated data and backups
- **CloudFront**: Global edge locations
- **Route 53**: Health checks and failover routing

## Recovery Procedures

### Scenario 1: Complete Region Failure

#### Detection (0-5 minutes)
- **Automated Monitoring**: CloudWatch alarms trigger
- **Health Checks**: Route 53 detects primary region failure
- **External Monitoring**: Pingdom/StatusCake alerts
- **Customer Reports**: Support ticket escalation

#### Response Team Activation (5-15 minutes)
```bash
# Emergency Response Team
- Incident Commander: CTO/Lead DevOps
- Technical Lead: Senior Developer
- Communications: Customer Success Manager
- External: AWS Support (if needed)
```

#### Immediate Actions (15-30 minutes)
1. **Confirm Scope**: Validate region-wide outage vs. service-specific
2. **Activate DR Site**: Switch to us-west-2 region
3. **Update DNS**: Route 53 failover to DR environment
4. **Notify Stakeholders**: Internal team and key customers

#### Technical Recovery (30 minutes - 4 hours)

**Step 1: Activate DR Infrastructure (30-60 minutes)**
```bash
# Deploy infrastructure in DR region
aws cloudformation create-stack \
  --stack-name lyo-infrastructure-dr \
  --template-body file://aws/cloudformation/lyo-infrastructure.yaml \
  --parameters ParameterKey=Environment,ParameterValue=production-dr \
               ParameterKey=ProjectName,ParameterValue=lyo-saas-dr \
               ParameterKey=VpcCidr,ParameterValue=10.1.0.0/16 \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

# Wait for infrastructure completion
aws cloudformation wait stack-create-complete \
  --stack-name lyo-infrastructure-dr \
  --region us-west-2
```

**Step 2: Restore Database (60-120 minutes)**
```bash
# Promote read replica to primary
aws rds promote-read-replica \
  --db-instance-identifier lyo-saas-production-postgres-replica \
  --backup-retention-period 7 \
  --region us-west-2

# Verify database connectivity
aws rds describe-db-instances \
  --db-instance-identifier lyo-saas-production-postgres-replica \
  --region us-west-2 \
  --query 'DBInstances[0].Endpoint.Address'
```

**Step 3: Deploy Application (120-180 minutes)**
```bash
# Deploy application stack
aws cloudformation create-stack \
  --stack-name lyo-application-dr \
  --template-body file://aws/cloudformation/lyo-application.yaml \
  --parameters ParameterKey=Environment,ParameterValue=production-dr \
               ParameterKey=ImageUri,ParameterValue=$ECR_IMAGE_URI \
               ParameterKey=InfrastructureStackName,ParameterValue=lyo-infrastructure-dr \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

# Wait for deployment
aws ecs wait services-stable \
  --cluster lyo-saas-dr \
  --services lyo-saas-dr \
  --region us-west-2
```

**Step 4: Update DNS and Verify (180-240 minutes)**
```bash
# Update Route 53 to point to DR region
aws route53 change-resource-record-sets \
  --hosted-zone-id $HOSTED_ZONE_ID \
  --change-batch file://dns-failover.json

# Verify application health
curl -f https://api.lyo-booking.com/health
```

### Scenario 2: Database Corruption/Failure

#### Detection and Assessment (0-15 minutes)
- Database connection failures
- Data integrity alerts
- Application error rates spike
- Backup verification

#### Recovery Actions (15 minutes - 2 hours)

**Option A: Point-in-Time Recovery (Preferred)**
```bash
# Create new DB instance from PITR
aws rds restore-db-instance-to-point-in-time \
  --db-instance-identifier lyo-postgres-restored-$(date +%Y%m%d) \
  --source-db-instance-identifier lyo-saas-production-postgres \
  --restore-time $(date -d '15 minutes ago' --iso-8601) \
  --db-instance-class db.t3.small \
  --no-multi-az

# Wait for restoration
aws rds wait db-instance-available \
  --db-instance-identifier lyo-postgres-restored-$(date +%Y%m%d)
```

**Option B: Backup Restoration**
```bash
# Restore from latest automated backup
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier lyo-postgres-restored-$(date +%Y%m%d) \
  --db-snapshot-identifier lyo-production-snapshot-latest \
  --db-instance-class db.t3.small
```

**Update Application Connection**
```bash
# Update database URL in Secrets Manager
aws secretsmanager update-secret \
  --secret-id lyo-saas/production/app-secrets \
  --secret-string '{
    "DATABASE_URL": "postgresql://lyoadmin:password@new-endpoint:5432/lyo_production",
    "REDIS_URL": "existing-redis-url",
    "OPENAI_API_KEY": "existing-key"
  }'

# Force ECS service update to pick up new secrets
aws ecs update-service \
  --cluster lyo-saas-production \
  --service lyo-saas-production \
  --force-new-deployment
```

### Scenario 3: Application Security Breach

#### Immediate Response (0-30 minutes)
1. **Isolate Systems**: Block suspicious traffic at WAF
2. **Preserve Evidence**: Take EBS snapshots, export logs
3. **Change Credentials**: Rotate all API keys and passwords
4. **Notify Authorities**: GDPR compliance if customer data affected

#### Security Incident Response
```bash
# Block suspicious IPs at WAF
aws wafv2 update-web-acl \
  --scope REGIONAL \
  --id $WAF_ACL_ID \
  --default-action Allow={} \
  --rules file://emergency-block-rules.json

# Rotate secrets immediately
aws secretsmanager rotate-secret \
  --secret-id lyo-saas/production/app-secrets \
  --force-rotate-immediately

# Export security logs
aws logs export-task \
  --task-name security-incident-$(date +%Y%m%d) \
  --log-group-name /aws/ecs/lyo-saas-production \
  --from $(date -d '24 hours ago' +%s)000 \
  --to $(date +%s)000 \
  --destination security-incident-logs-bucket
```

## Recovery Testing

### Monthly DR Tests (Automated)
- **Database Recovery**: Restore from backup to test environment
- **Application Deployment**: Deploy to DR region
- **DNS Failover**: Test Route 53 health check failover
- **Monitoring**: Verify all alerting systems

### Quarterly DR Exercises (Manual)
- **Full Region Failover**: Complete failover simulation
- **Security Incident Response**: Breach simulation
- **Communication Tests**: Stakeholder notification drill
- **Documentation Review**: Update procedures based on lessons learned

### DR Testing Script
```bash
#!/bin/bash
# DR Testing Automation Script

# Test 1: Database Backup Restore
echo "Testing database backup restore..."
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier lyo-dr-test-$(date +%Y%m%d) \
  --db-snapshot-identifier $(aws rds describe-db-snapshots \
    --db-instance-identifier lyo-saas-production-postgres \
    --snapshot-type automated \
    --query 'DBSnapshots[0].DBSnapshotIdentifier' \
    --output text) \
  --db-instance-class db.t3.micro

# Test 2: Application Deployment to DR region
echo "Testing application deployment to DR region..."
aws cloudformation validate-template \
  --template-body file://aws/cloudformation/lyo-infrastructure.yaml

# Test 3: DNS Failover
echo "Testing DNS health check..."
aws route53 get-health-check \
  --health-check-id $HEALTH_CHECK_ID

# Cleanup test resources
echo "Cleaning up test resources..."
aws rds delete-db-instance \
  --db-instance-identifier lyo-dr-test-$(date +%Y%m%d) \
  --skip-final-snapshot
```

## Communication Plan

### Internal Communication
- **Incident Commander**: Coordinates response, external communications
- **Technical Team**: Engineering lead, DevOps, security
- **Business Team**: Customer success, sales, management
- **Legal/Compliance**: GDPR officer, legal counsel

### External Communication

#### Customer Communication Templates

**Initial Incident Notification (< 30 minutes)**
```
Subject: Service Disruption - Lyo Booking Assistant

Dear [Customer Name],

We are experiencing a service disruption affecting the Lyo booking assistant. 
Our team is actively working to restore service.

Estimated Resolution: Within 4 hours
Status Page: https://status.lyo-booking.com
Updates: Every 30 minutes

We apologize for the inconvenience.

The Lyo Team
```

**Service Restored Notification**
```
Subject: Service Restored - Lyo Booking Assistant

Dear [Customer Name],

Service has been fully restored. All booking functionality is now operational.

Incident Summary:
- Started: [Time]
- Resolved: [Time] 
- Root Cause: [Brief explanation]
- Preventive Measures: [Actions taken]

Post-incident report: [Link]

Thank you for your patience.

The Lyo Team
```

## Recovery Validation

### Health Check Procedures
1. **Application Health**: `/health` endpoint returns 200 OK
2. **Database Connectivity**: All tables accessible, data integrity verified
3. **Redis Cache**: Session management functional
4. **WhatsApp Webhooks**: Test message flow end-to-end
5. **Google Calendar**: Appointment creation/modification working
6. **OpenAI Integration**: LLM responses functioning

### Performance Validation
```bash
# Application response time
curl -w "@curl-format.txt" -s -o /dev/null https://api.lyo-booking.com/health

# Database performance
time psql $DATABASE_URL -c "SELECT COUNT(*) FROM lyo_users;"

# Load test (basic)
ab -n 100 -c 10 https://api.lyo-booking.com/health
```

### Data Integrity Checks
```sql
-- Check data consistency after recovery
SELECT 
    'users' as table_name, 
    COUNT(*) as record_count, 
    MAX(created_at) as latest_record
FROM lyo_users
UNION ALL
SELECT 
    'conversations' as table_name, 
    COUNT(*) as record_count, 
    MAX(last_message_at) as latest_record  
FROM lyo_conversations
UNION ALL
SELECT 
    'appointments' as table_name, 
    COUNT(*) as record_count, 
    MAX(created_at) as latest_record
FROM lyo_appointments;

-- Verify recent data (last 24 hours)
SELECT COUNT(*) as recent_conversations
FROM lyo_conversations 
WHERE last_message_at > NOW() - INTERVAL '24 hours';
```

## Continuous Improvement

### Post-Incident Review Process
1. **Timeline Documentation**: Detailed incident timeline
2. **Root Cause Analysis**: 5-whys analysis, contributing factors
3. **Response Evaluation**: What worked well, what didn't
4. **Action Items**: Specific improvements with owners and timelines
5. **Documentation Updates**: Update runbooks and procedures

### Monthly DR Metrics Review
- **RTO/RPO Achievement**: Actual vs. target recovery times
- **Test Success Rate**: Percentage of successful DR tests
- **Detection Time**: Time to identify incidents
- **Response Time**: Time to begin recovery actions
- **Customer Impact**: Affected customers, support tickets

### Annual DR Plan Review
- **Technology Updates**: New AWS services, architecture changes
- **Business Requirements**: Updated RTO/RPO targets
- **Compliance Requirements**: New regulatory requirements
- **Team Changes**: Updated contact information, responsibilities
- **Lessons Learned**: Incorporate learnings from incidents and tests

## Contact Information

### Emergency Response Team
```
Incident Commander: [Name] - +[Phone] - [Email]
Technical Lead: [Name] - +[Phone] - [Email]
DevOps Lead: [Name] - +[Phone] - [Email]
Security Lead: [Name] - +[Phone] - [Email]
Customer Success: [Name] - +[Phone] - [Email]
```

### External Contacts
```
AWS Support: Enterprise Support Case
Domain Registrar: [Registrar Support]
SSL Certificate Provider: [Provider Support]
Monitoring Service: [Service Support]
```

### Escalation Matrix
- **30 minutes**: No progress → Escalate to management
- **2 hours**: Significant customer impact → CEO notification
- **4 hours**: RTO exceeded → Executive emergency meeting
- **Data Breach**: Immediate escalation to legal/compliance

---

*This disaster recovery plan is reviewed quarterly and updated as needed. Last updated: [Date]*