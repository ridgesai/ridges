#!/bin/bash

# Retry upload script - runs until successful
# Logs to retry_upload.log for monitoring

LOG_FILE="/root/ridges-shardul/retry_upload.log"
SCRIPT_DIR="/root/ridges-shardul"
AGENT_NAME="novus-agent"

cd "$SCRIPT_DIR"

echo "$(date): Starting upload retry script" >> "$LOG_FILE"
echo "$(date): Will retry every 1 minute until successful" >> "$LOG_FILE"

attempt=1

while true; do
    echo "$(date): Attempt #$attempt - Starting upload..." >> "$LOG_FILE"
    
    # Run the upload command with the agent name pre-filled
    output=$(echo "$AGENT_NAME" | timeout 300 ./ridges.py upload 2>&1)
    exit_code=$?
    
    echo "$(date): Upload output:" >> "$LOG_FILE"
    echo "$output" >> "$LOG_FILE"
    echo "$(date): Exit code: $exit_code" >> "$LOG_FILE"
    
    # Check if upload was successful (no "No stage 1 screeners" error and exit code 0)
    if [[ $exit_code -eq 0 ]] && [[ ! "$output" =~ "No stage 1 screeners available" ]] && [[ ! "$output" =~ "Upload failed" ]]; then
        echo "$(date): ✅ Upload successful! Exiting retry loop." >> "$LOG_FILE"
        echo "$(date): Final successful output:" >> "$LOG_FILE"
        echo "$output" >> "$LOG_FILE"
        break
    else
        echo "$(date): ❌ Upload failed or screeners unavailable. Will retry in 1 minute..." >> "$LOG_FILE"
        echo "$(date): Waiting 60 seconds before next attempt..." >> "$LOG_FILE"
        
        # Wait 1 minute (60 seconds)
        sleep 60
        
        attempt=$((attempt + 1))
    fi
done

echo "$(date): Upload retry script completed successfully!" >> "$LOG_FILE"
