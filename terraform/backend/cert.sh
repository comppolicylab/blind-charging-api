#!/usr/bin/env bash

ACTION=""
if [ "$1" == "present" ]; then
    ACTION="create"
elif [ "$1" == "cleanup" ]; then
    ACTION="delete"
else
    echo "Invalid action $1"
    exit 1
fi

CHALLENGE_FILE="challenge.log"
# Keep a running log of cert challenges
if [ ! -f "$CHALLENGE_FILE" ]; then
    touch "$CHALLENGE_FILE"
fi

echo "=== ACME CERTIFICATE CHALLENGE REQUEST ===" >> "$CHALLENGE_FILE"
echo "Time: $(date)" >> "$CHALLENGE_FILE"
echo "Action: $ACTION" >> "$CHALLENGE_FILE"
echo "Method: DNS TXT record" >> "$CHALLENGE_FILE"
echo "Key: $2" >> "$CHALLENGE_FILE"
echo "Value: $3" >> "$CHALLENGE_FILE"
echo "===========================================" >> "$CHALLENGE_FILE"
