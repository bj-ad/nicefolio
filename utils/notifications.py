"""
Notification Utility for Portfolio Tracker
==========================================
Supports multiple notification channels:
- Email (SMTP)
- Home Assistant (REST API)
- Telegram (Bot API)
- Custom Webhooks

Configuration in app_config.yaml under 'notifications' section.
"""

import os
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, List
from datetime import datetime, timezone
from utils.logging_config import get_logger
from utils.app_config import load_app_config

logger = get_logger(__name__)


class NotificationService:
    """
    Multi-channel notification service.
    
    Supports:
    - Email (via SMTP)
    - Home Assistant (via REST API)
    - Telegram (via Bot API)
    - Custom Webhooks
    """
    
    def __init__(self):
        """Load notification configuration from app_config.yaml"""
        self.config = load_app_config().get('notifications', {})
        self.enabled = self.config.get('enabled', False)
        self.channels = self.config.get('channels', {})
        
        if not self.enabled:
            logger.info("Notifications are disabled in config")
    
    def send_job_failure_alert(
        self,
        job_name: str,
        error_message: str,
        job_type: str = "daily",
        additional_info: Optional[Dict] = None
    ) -> bool:
        """
        Send alert about job failure.
        
        Args:
            job_name: Name of the failed job (e.g., "Data Sync", "Weekly Lot Recreation")
            error_message: Error message or exception details
            job_type: Type of job ("daily" or "weekly")
            additional_info: Optional dict with additional context
        
        Returns:
            bool: True if at least one notification was sent successfully
        """
        if not self.enabled:
            logger.debug("Notifications disabled - skipping alert")
            return False
        
        # Build notification message
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        severity = "🚨 CRITICAL" if job_type == "weekly" else "⚠️ WARNING"
        
        subject = f"NiceFolio: {job_name} Failed"
        
        message = f"""
{severity} Job Failure Alert

Job: {job_name}
Type: {job_type.upper()}
Time: {timestamp}

Error:
{error_message}
"""
        
        if additional_info:
            message += "\n\nAdditional Information:\n"
            for key, value in additional_info.items():
                message += f"  {key}: {value}\n"
        
        message += f"""
Please check the logs for more details:
  docker logs nicefolio_worker --tail 100

Or in the app logs:
  logs/app.log
"""
        
        # Send to all configured channels
        success = False
        
        if self.channels.get('email', {}).get('enabled'):
            if self._send_email(subject, message):
                success = True
        
        if self.channels.get('home_assistant', {}).get('enabled'):
            if self._send_home_assistant(subject, message, severity):
                success = True
        
        if self.channels.get('telegram', {}).get('enabled'):
            if self._send_telegram(subject, message):
                success = True
        
        if self.channels.get('webhook', {}).get('enabled'):
            if self._send_webhook(job_name, error_message, job_type, severity):
                success = True
        
        return success
    
    def send_job_success_summary(
        self,
        job_name: str,
        summary: Dict,
        job_type: str = "daily"
    ) -> bool:
        """
        Send summary of successful job completion (optional).
        
        Args:
            job_name: Name of completed job
            summary: Dict with job statistics (e.g., items synced, errors)
            job_type: Type of job ("daily" or "weekly")
        
        Returns:
            bool: True if at least one notification was sent successfully
        """
        if not self.enabled:
            return False
        
        # Only send success notifications if configured
        send_success = self.config.get('send_success_notifications', False)
        if not send_success:
            return False
        
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        subject = f"NiceFolio: {job_name} Completed"
        
        message = f"""
✅ Job Completed Successfully

Job: {job_name}
Type: {job_type.upper()}
Time: {timestamp}

Summary:
"""
        
        for key, value in summary.items():
            message += f"  {key}: {value}\n"
        
        # Send to configured channels (usually only Home Assistant for success)
        success = False
        
        if self.channels.get('home_assistant', {}).get('enabled'):
            if self._send_home_assistant(subject, message, "✅ SUCCESS"):
                success = True
        
        return success
    
    def send_backup_report(self, output: str, success: bool) -> bool:
        """
        Send backup integrity check report.
        
        Args:
            output: Output from restic check command
            success: Whether the check passed
        """
        if not self.enabled:
            return False
            
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        status_icon = "✅" if success else "❌"
        status_text = "PASSED" if success else "FAILED"
        
        subject = f"NiceFolio: Backup Integrity Check {status_text}"
        
        message = f"""
{status_icon} Backup Integrity Check {status_text}

Time: {timestamp}

Restic Check Output:
----------------------------------------
{output}
----------------------------------------
"""
        
        # Send to all channels
        sent = False
        if self.channels.get('email', {}).get('enabled'):
            if self._send_email(subject, message):
                sent = True
        
        if self.channels.get('home_assistant', {}).get('enabled'):
            if self._send_home_assistant(subject, message, status_text):
                sent = True
                
        if self.channels.get('telegram', {}).get('enabled'):
            if self._send_telegram(subject, message):
                sent = True

        return sent

    def _send_email(self, subject: str, message: str) -> bool:
        """Send email notification via SMTP"""
        try:
            email_config = self.channels['email']
            
            smtp_server = email_config.get('smtp_server')
            smtp_port = email_config.get('smtp_port', 587)
            smtp_user = email_config.get('smtp_user')
            smtp_password = os.getenv('SMTP_PASSWORD') or email_config.get('smtp_password')
            from_email = email_config.get('from_email', smtp_user)
            to_emails = email_config.get('to_emails', [])
            
            if not all([smtp_server, smtp_user, smtp_password, to_emails]):
                logger.warning("Email configuration incomplete - skipping email notification")
                return False
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = ', '.join(to_emails)
            msg['Subject'] = subject
            msg.attach(MIMEText(message, 'plain'))
            
            # Send via SMTP
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email notification sent to {', '.join(to_emails)}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}", exc_info=True)
            return False
    
    def _send_home_assistant(self, title: str, message: str, severity: str) -> bool:
        """Send notification to Home Assistant via REST API"""
        try:
            ha_config = self.channels['home_assistant']
            
            url = ha_config.get('url')
            token = os.getenv('HOME_ASSISTANT_TOKEN') or ha_config.get('token')
            service = ha_config.get('service', 'notify.notify')  # Default notification service
            
            if not all([url, token]):
                logger.warning("Home Assistant configuration incomplete - skipping notification")
                return False
            
            # Call Home Assistant REST API
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'title': title,
                'message': message,
                'data': {
                    'priority': 'high' if 'CRITICAL' in severity else 'normal',
                    'tag': 'portfolio_tracker_job',
                    'group': 'portfolio_tracker'
                }
            }
            
            # Use the configured service endpoint
            api_url = f"{url}/api/services/{service.replace('.', '/')}"
            
            response = requests.post(api_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Home Assistant notification sent via {service}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Home Assistant notification: {e}", exc_info=True)
            return False
    
    def _send_telegram(self, subject: str, message: str) -> bool:
        """Send notification via Telegram Bot API"""
        try:
            telegram_config = self.channels['telegram']
            
            bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or telegram_config.get('bot_token')
            chat_id = telegram_config.get('chat_id')
            
            if not all([bot_token, chat_id]):
                logger.warning("Telegram configuration incomplete - skipping notification")
                return False
            
            # Format message for Telegram (with HTML markup)
            telegram_message = f"<b>{subject}</b>\n\n{message}"
            
            # Send via Telegram Bot API
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': telegram_message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Telegram notification sent to chat {chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}", exc_info=True)
            return False
    
    def _send_webhook(
        self,
        job_name: str,
        error_message: str,
        job_type: str,
        severity: str
    ) -> bool:
        """Send notification to custom webhook"""
        try:
            webhook_config = self.channels['webhook']
            
            url = webhook_config.get('url')
            method = webhook_config.get('method', 'POST').upper()
            headers = webhook_config.get('headers', {})
            
            if not url:
                logger.warning("Webhook URL not configured - skipping webhook notification")
                return False
            
            # Prepare payload
            payload = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'severity': severity,
                'job_name': job_name,
                'job_type': job_type,
                'error_message': error_message,
                'source': 'portfolio_tracker'
            }
            
            # Send request
            if method == 'POST':
                response = requests.post(url, json=payload, headers=headers, timeout=10)
            elif method == 'GET':
                response = requests.get(url, params=payload, headers=headers, timeout=10)
            else:
                logger.error(f"Unsupported webhook method: {method}")
                return False
            
            response.raise_for_status()
            
            logger.info(f"Webhook notification sent to {url}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}", exc_info=True)
            return False


# Singleton instance
_notification_service = None


def get_notification_service() -> NotificationService:
    """Get or create singleton notification service instance"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service


# Convenience functions
def send_job_failure_alert(
    job_name: str,
    error_message: str,
    job_type: str = "daily",
    additional_info: Optional[Dict] = None
) -> bool:
    """Convenience function to send job failure alert"""
    service = get_notification_service()
    return service.send_job_failure_alert(job_name, error_message, job_type, additional_info)


def send_job_success_summary(
    job_name: str,
    summary: Dict,
    job_type: str = "daily"
) -> bool:
    """Convenience function to send job success summary"""
    service = get_notification_service()
    return service.send_job_success_summary(job_name, summary, job_type)


def send_transaction_ingestion_summary(
    accounts_synced: Dict[str, Dict],
    total_new: int = 0,
    total_reviewed: int = 0
) -> bool:
    """
    Send summary of unreviewed transactions needing review.
    
    Args:
        accounts_synced: Dict with account details:
            {
                'IBKR': {'new': 5, 'types': {'buy': 3, 'sell': 2}, 'failed': 0},
                'Binance.th': {'new': 12, 'types': {'deposit': 2, 'trade': 10}, 'failed': 0},
                'Crypto Wallets': {'new': 3, 'types': {'transfer_in': 2, 'transfer_out': 1}},
                ...
            }
        total_new: Total unreviewed transactions across all accounts
        total_reviewed: Not used (kept for API compatibility, always 0)
    
    Returns:
        bool: True if notification sent successfully
    """
    service = get_notification_service()
    
    if not service.enabled:
        return False
    
    # Check if transaction notifications are enabled
    send_tx_notifications = service.config.get('send_transaction_notifications', False)
    if not send_tx_notifications:
        return False
    
    # Don't send if no unreviewed transactions
    if total_new == 0:
        logger.debug("No unreviewed transactions - skipping notification")
        return False
    
    # Build notification message
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    subject = f"NiceFolio: {total_new} Transaction{'s' if total_new != 1 else ''} Need Review"
    
    message = f"""
📊 Transaction Review Reminder

Time: {timestamp}
Unreviewed Transactions: {total_new}

"""
    
    # Add per-account breakdown
    if accounts_synced:
        message += "Account Breakdown:\n"
        message += "=" * 50 + "\n\n"
        
        for account_name, stats in accounts_synced.items():
            new_count = stats.get('new', 0)
            failed_count = stats.get('failed', 0)
            
            if new_count > 0:
                message += f"📁 {account_name}:\n"
                message += f"   Unreviewed: {new_count}\n"
                
                # Add symbol breakdown
                symbols = stats.get('symbols', {})
                if symbols:
                    message += "   Symbols:\n"
                    for symbol, count in sorted(symbols.items(), key=lambda x: (-x[1], x[0])):
                        message += f"      • {symbol}: {count}\n"
                
                # Add transaction type breakdown
                types = stats.get('types', {})
                if types:
                    message += "   Types:\n"
                    for tx_type, count in sorted(types.items()):
                        message += f"      • {tx_type}: {count}\n"
                
                if failed_count > 0:
                    message += f"   ⚠️  Failed: {failed_count}\n"
                
                message += "\n"
    
    # Add reminder to review
    message += f"""
📝 Action Required:

Please review and mark the {total_new} unreviewed transaction(s):
  • Go to the Transactions Overview app
  • Review transaction details
  • Mark as reviewed when confirmed

This helps maintain data quality and ensures accurate portfolio tracking.
"""
    
    # Send email notification
    if service.channels.get('email', {}).get('enabled'):
        return service._send_email(subject, message)
    
    return False


def send_backup_report(output: str, success: bool) -> bool:
    """
    Send backup integrity check report.
    
    Args:
        output: Output from restic check command
        success: Whether the check passed
    """
    service = NotificationService()
    return service.send_backup_report(output, success)
