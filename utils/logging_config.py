import os
import time
import logging
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'app.log')

def configure_logging():
    """
    Configures the root logger to write to a single, rotating log file.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if logger is already configured
    if not root_logger.hasHandlers():
        file_handler = TimedRotatingFileHandler(
            LOG_FILE,
            when="midnight",
            interval=1,
            backupCount=7
        )
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Also add a console handler for immediate feedback during development
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

def get_logger(name: str):
    """
    Returns a logger instance for the given name.
    The root logger should be configured before calling this function.
    """
    return logging.getLogger(name)

def rotate_log_file_daily(file_path: str | Path, backup_count: int = 7):
    """
    Rotate a log file daily using TimedRotatingFileHandler logic.
    This ensures consistency with app.log rotation.
    
    Note: Since the application runs as the admin user, no ownership
    changes are needed - files are automatically created with correct ownership.
    
    Args:
        file_path: Path to the log file
        backup_count: Number of daily backups to keep (default 7)
    """
    path = Path(file_path)
    
    # Create the file if it doesn't exist
    if not path.exists():
        path.touch()
        return

    # Use a local logger for this utility function to avoid circular dependency issues
    local_logger = logging.getLogger("log_rotator")

    try:
        # Use TimedRotatingFileHandler to manage rotation logic
        # This reuses the standard library code used for app.log
        handler = TimedRotatingFileHandler(
            str(path),
            when="midnight",
            interval=1,
            backupCount=backup_count
        )
        
        try:
            # Check if rotation is needed based on file's modification time
            # rolloverAt is computed by the handler upon initialization
            current_time = int(time.time())
            
            if current_time >= handler.rolloverAt:
                local_logger.info(f"Rotating log file (daily): {path}")
                handler.doRollover()
                local_logger.info(f"Successfully rotated {path}")
            
        finally:
            handler.close()
            
    except Exception as e:
        local_logger.error(f"Failed to rotate log file {path}: {e}")

# Configure logging when this module is imported
configure_logging()