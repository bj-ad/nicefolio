#!/usr/bin/env python3
"""
Worker Health Check Script

Verifies that the worker scheduler is alive and functioning.
Used by Docker healthcheck to monitor worker container health.

Exit codes:
  0 - Healthy: Scheduler is running and recent heartbeat detected
  1 - Unhealthy: Scheduler not running or heartbeat stale
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Heartbeat file path (written by scheduler every minute)
HEARTBEAT_FILE = Path("/tmp/worker_heartbeat.txt")

# Maximum age for heartbeat (5 minutes = scheduler may be stuck)
MAX_HEARTBEAT_AGE_MINUTES = 5


def check_worker_health() -> bool:
    """
    Check if worker is healthy by verifying heartbeat file freshness.
    
    Returns:
        bool: True if healthy, False otherwise
    """
    try:
        # Check if heartbeat file exists
        if not HEARTBEAT_FILE.exists():
            print(f"UNHEALTHY: Heartbeat file not found at {HEARTBEAT_FILE}", file=sys.stderr)
            return False
        
        # Read heartbeat timestamp
        heartbeat_time_str = HEARTBEAT_FILE.read_text().strip()
        heartbeat_time = datetime.fromisoformat(heartbeat_time_str)
        
        # Check heartbeat age
        now = datetime.now()
        age = now - heartbeat_time
        
        if age > timedelta(minutes=MAX_HEARTBEAT_AGE_MINUTES):
            print(
                f"UNHEALTHY: Heartbeat is {age.total_seconds() / 60:.1f} minutes old "
                f"(max: {MAX_HEARTBEAT_AGE_MINUTES} min)",
                file=sys.stderr
            )
            return False
        
        # Healthy
        print(f"HEALTHY: Heartbeat age {age.total_seconds():.0f}s")
        return True
        
    except Exception as e:
        print(f"UNHEALTHY: Error checking heartbeat: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Exit 0 if healthy, 1 if unhealthy (Docker healthcheck convention)
    sys.exit(0 if check_worker_health() else 1)
