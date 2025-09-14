"""Gmail monitoring service for continuous email processing."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import uuid

from gmail.client import GmailClient, create_gmail_client
from database.models import InvoiceProcessing, ProcessingStatus
from utils.logger import get_processing_logger
from config import get_settings


logger = logging.getLogger(__name__)


class GmailMonitor:
    """Monitors Gmail for new emails and triggers processing."""
    
    def __init__(self):
        self.settings = get_settings()
        self.client: Optional[GmailClient] = None
        self.is_running = False
        self.last_check = None
        self.processed_messages = set()  # Cache of processed message IDs
        
    async def initialize(self) -> bool:
        """Initialize the Gmail monitor."""
        try:
            logger.info("Initializing Gmail monitor...")
            
            # Create Gmail client
            self.client = await create_gmail_client()
            if not self.client:
                logger.error("Failed to create Gmail client")
                return False
            
            # Test connection
            connection_ok = await self.client.test_connection()
            if not connection_ok:
                logger.error("Gmail connection test failed")
                return False
            
            logger.info("Gmail monitor initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Gmail monitor: {e}")
            return False
    
    async def start_monitoring(self) -> None:
        """Start continuous monitoring of Gmail."""
        if not self.client:
            logger.error("Gmail client not initialized")
            return
        
        self.is_running = True
        logger.info("Starting Gmail monitoring...")
        
        try:
            while self.is_running:
                try:
                    await self._check_for_new_emails()
                    
                    # Wait before next check (configurable interval)
                    check_interval = 60  # Check every 60 seconds
                    await asyncio.sleep(check_interval)
                    
                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    # Wait a bit longer before retrying on error
                    await asyncio.sleep(300)  # 5 minutes
                    
        except asyncio.CancelledError:
            logger.info("Gmail monitoring cancelled")
        except Exception as e:
            logger.error(f"Gmail monitoring stopped due to error: {e}")
        finally:
            self.is_running = False
            logger.info("Gmail monitoring stopped")
    
    async def stop_monitoring(self) -> None:
        """Stop Gmail monitoring."""
        self.is_running = False
        logger.info("Stopping Gmail monitoring...")
    
    async def _check_for_new_emails(self) -> None:
        """Check for new emails with PDF attachments."""
        check_time = datetime.utcnow()
        
        try:
            # Determine time range for check
            if self.last_check:
                # Check since last check + small overlap
                hours_back = max(1, int((check_time - self.last_check).total_seconds() / 3600) + 1)
            else:
                # First check - look back 24 hours
                hours_back = 24
            
            logger.debug(f"Checking for emails from last {hours_back} hours")
            
            # Get emails with PDF attachments
            emails = await self.client.get_recent_emails_with_attachments(hours_back=hours_back)
            
            if not emails:
                logger.debug("No new emails with PDF attachments found")
                self.last_check = check_time
                return
            
            logger.info(f"Found {len(emails)} emails with PDF attachments")
            
            # Process each email
            new_emails = 0
            for email in emails:
                # Skip if already processed
                if email['id'] in self.processed_messages:
                    continue
                
                # Check if already in database
                if await self._is_message_already_processed(email['id']):
                    self.processed_messages.add(email['id'])
                    continue
                
                # Process new email
                await self._process_new_email(email)
                new_emails += 1
                self.processed_messages.add(email['id'])
            
            if new_emails > 0:
                logger.info(f"Queued {new_emails} new emails for processing")
            else:
                logger.debug("No new emails to process")
            
            self.last_check = check_time
            
        except Exception as e:
            logger.error(f"Error checking for new emails: {e}")
    
    async def _process_new_email(self, email: Dict[str, Any]) -> None:
        """Process a new email by creating processing records."""
        correlation_id = str(uuid.uuid4())
        proc_logger = get_processing_logger("gmail_processing", correlation_id)
        
        try:
            proc_logger.info(
                f"Processing new email",
                email_id=email['id'],
                sender=email['sender'],
                subject=email['subject'],
                pdf_count=len(email.get('pdf_attachments', []))
            )
            
            # Create processing record for each PDF attachment
            for attachment in email.get('pdf_attachments', []):
                await self._create_processing_record(email, attachment, correlation_id)
            
            proc_logger.info("Email processing queued successfully")
            
        except Exception as e:
            proc_logger.error(f"Failed to process email: {e}")
    
    async def _create_processing_record(
        self,
        email: Dict[str, Any],
        attachment: Dict[str, Any],
        correlation_id: str
    ) -> None:
        """Create a processing record in the database."""
        try:
            # Extract email timestamp
            email_timestamp = datetime.fromtimestamp(email['timestamp'])
            
            processing_record = InvoiceProcessing(
                gmail_message_id=email['id'],
                sender_email=email['sender'],
                subject=email['subject'],
                pdf_filename=attachment['filename'],
                pdf_size_bytes=attachment.get('size', 0),
                processing_status=ProcessingStatus.PENDING.value,
                created_at=email_timestamp,
                updated_at=datetime.utcnow()
            )
            
            # TODO: Save to database
            # For now, just log the creation
            logger.info(
                f"Created processing record for {attachment['filename']}",
                correlation_id=correlation_id,
                email_id=email['id'],
                filename=attachment['filename']
            )
            
        except Exception as e:
            logger.error(f"Failed to create processing record: {e}")
    
    async def _is_message_already_processed(self, message_id: str) -> bool:
        """Check if message is already in database."""
        # TODO: Implement database check
        # For now, return False to allow processing
        return False
    
    async def get_monitoring_stats(self) -> Dict[str, Any]:
        """Get monitoring statistics."""
        stats = {
            "is_running": self.is_running,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "processed_messages_cache_size": len(self.processed_messages),
            "client_initialized": self.client is not None
        }
        
        # Get Gmail client stats if available
        if self.client:
            try:
                gmail_stats = await self.client.get_processing_stats()
                stats.update(gmail_stats)
            except Exception as e:
                stats["gmail_stats_error"] = str(e)
        
        return stats
    
    async def process_single_message(self, message_id: str) -> Dict[str, Any]:
        """Process a single message manually (for testing/debugging)."""
        if not self.client:
            return {"error": "Gmail client not initialized"}
        
        try:
            logger.info(f"Manually processing message {message_id}")
            
            # Get message details
            email = await self.client.get_message_by_id(message_id)
            if not email:
                return {"error": "Message not found or no PDF attachments"}
            
            # Process the email
            await self._process_new_email(email)
            
            return {
                "success": True,
                "message_id": message_id,
                "pdf_attachments": len(email.get('pdf_attachments', [])),
                "processed_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to manually process message {message_id}: {e}")
            return {"error": str(e)}


# Global monitor instance
_monitor_instance: Optional[GmailMonitor] = None


async def get_gmail_monitor() -> GmailMonitor:
    """Get or create Gmail monitor singleton."""
    global _monitor_instance
    
    if _monitor_instance is None:
        _monitor_instance = GmailMonitor()
        initialized = await _monitor_instance.initialize()
        if not initialized:
            raise RuntimeError("Failed to initialize Gmail monitor")
    
    return _monitor_instance


async def start_gmail_monitoring():
    """Start Gmail monitoring service."""
    monitor = await get_gmail_monitor()
    await monitor.start_monitoring()


async def stop_gmail_monitoring():
    """Stop Gmail monitoring service."""
    global _monitor_instance
    if _monitor_instance:
        await _monitor_instance.stop_monitoring()


if __name__ == "__main__":
    # Allow running monitor directly for testing
    asyncio.run(start_gmail_monitoring())