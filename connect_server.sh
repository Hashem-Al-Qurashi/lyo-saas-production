#!/usr/bin/expect -f

# SSH connection script for Hetzner server
set timeout 30

spawn ssh -o StrictHostKeyChecking=no root@135.181.249.116

expect "password:"
send "ah3tAL37sPJgx7dRiV44\r"

expect "root@*"
send "echo 'Connected to Hetzner server successfully!'\r"

expect "root@*"
send "uname -a && cat /etc/os-release | head -3\r"

expect "root@*"
send "exit\r"

expect eof