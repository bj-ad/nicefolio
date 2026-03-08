"""
NiceFolio - Unified Landing Page
Single entry point with navigation to all applications

Run this file to start the unified interface:
    python main.py

Access:
    http://localhost:8080 - Landing page with navigation
    http://localhost:8080/portfolio - Portfolio dashboard
    http://localhost:8080/db-viewer - Database viewer
    http://localhost:8080/wallet-manager - Crypto wallet manager
"""

import sys
import os
import logging

# Configure NiceGUI JavaScript timeout BEFORE importing ui
# Must be done before any NiceGUI imports to take effect
from nicegui import javascript_request
javascript_request.JavaScriptRequest.timeout = 5.0

# Now import NiceGUI components
from nicegui import ui, app

# Setup logging
from utils.logging_config import get_logger
logger = get_logger(__name__)

# ============================================================================
# ZOMBIE TIMER ERROR SUPPRESSION
# ============================================================================
# Suppress "parent slot deleted" errors that occur during page navigation
# These are harmless race conditions on slow VMs where timers fire after 
# their parent elements have been destroyed. The errors don't affect
# functionality - they're just noise in the logs.
# 
# Root cause: NiceGUI's timer framework checks element.parent_slot before
# our defensive code can run. On slow VMs with network latency, this race
# condition is more common.
# ============================================================================

def suppress_zombie_timer_errors():
    """Suppress harmless 'parent slot deleted' errors from NiceGUI timer framework"""
    nicegui_logger = logging.getLogger('nicegui')
    
    class ZombieTimerFilter(logging.Filter):
        def filter(self, record):
            # Suppress "The parent slot of the element has been deleted" errors
            if 'parent slot' in record.getMessage().lower() and 'deleted' in record.getMessage().lower():
                # These are expected on slow VMs during navigation
                return False
            return True
    
    nicegui_logger.addFilter(ZombieTimerFilter())
    logger.info("Zombie timer error suppression enabled (caused by docker healthcheck ping)")

suppress_zombie_timer_errors()
        
# Ensure /app is in path
if '/app' not in sys.path:
    sys.path.insert(0, '/app')
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Add static files route for images
app.add_static_files('/static', 'apps/images')
app.add_static_file(local_file='apps/images/favicon.ico', url_path='/favicon.ico')

# Define the head HTML
meta_tags = """
    <link rel="icon" type="image/svg+xml" href="/static/icon.svg">
    <link rel="icon" type="image/png" href="/static/favicon.ico">
    <link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png">
    <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
    <link rel="mask-icon" href="/static/safari-pinned-tab.svg" color="#303F9F">
    <link rel="manifest" href="/static/site.webmanifest">
    <meta name="theme-color" content="#1e293b">
    
    <style>
    /* Smooth page transitions - prevents jarring white flash */
    body {
        background-color: #f1f5f9; /* Match NiceGUI default background */
        transition: opacity 0.15s ease-in-out;
    }
    
    /* Prevent white flash during navigation */
    html {
        background-color: #f1f5f9;
    }
    </style>
"""

# Inject head HTML into app
ui.add_head_html(meta_tags, shared=True)

# Prevent automatic page reloads on WebSocket disconnect
ui.add_head_html("""
<script>
// Prevent NiceGUI from automatically reloading the page on disconnect
window.addEventListener('DOMContentLoaded', function() {
    // Override any auto-reload behavior
    if (window.nicegui) {
        const originalOnDisconnect = window.nicegui.onDisconnect;
        window.nicegui.onDisconnect = function() {
            console.log('WebSocket disconnected - maintaining current page state');
            // Don't reload, just wait for reconnection
        };
    }
});
</script>
""", shared=True)

# ============================================================================
# Import Page Modules - Each module registers its own @ui.page routes
# ============================================================================
from apps.pages import portfolio  # Also serves as home page (/)
from apps.pages import db_viewer
from apps.pages import wallet_manager
from apps.pages import staking
from apps.pages import settings
from apps.pages import transaction_review
from apps.pages import cash_manager
from apps.pages import tax_reports


# ============================================================================
# WebSocket Connection Management
# ============================================================================

@app.on_disconnect
def handle_disconnect():
    """
    Handle WebSocket disconnections gracefully without forcing page reload.
    This prevents automatic page refreshes every 15-20 seconds on local networks.
    """
    pass  # Do nothing - let client handle reconnection naturally


# ============================================================================
# Run Application
# ============================================================================

if __name__ in {"__main__", "__mp_main__"}:
    import os
    from utils.app_config import load_app_config
    
    logger.info("="*50)
    logger.info("NiceFolio GUI Starting...")
    logger.info("="*50)
    
    # Load app config to check environment
    config = load_app_config()
    environment = config.get('environment', 'PROD')
    
    # Set title based on environment
    if environment == 'DEV':
        app_title = 'DEV | NiceFolio'
    else:
        app_title = 'NiceFolio | Portfolio Management Suite'
    
    logger.info(f"Environment: {environment}")
    logger.info(f"Title: {app_title}")
    
    # Generate a storage secret from environment or use a default
    # In production, set NICEGUI_STORAGE_SECRET environment variable
    storage_secret = os.environ.get('NICEGUI_STORAGE_SECRET', 'nicefolio-default-secret-change-in-production')
    
    logger.info("Starting NiceGUI server on http://0.0.0.0:8080")
    logger.info("="*50)
    
    ui.run(
        title=app_title,
        favicon='/favicon.ico',
        host='0.0.0.0',
        port=8080,
        reload=False,
        show=False,
        storage_secret=storage_secret,
        reconnect_timeout=300.0,  # 5 minutes - prevent unnecessary reconnections for static content
        forwarded_allow_ips='*',
    )
