"""Gmail API client for email processing."""

import logging
import base64
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail.auth import GmailAuth
from config import get_settings
from utils.logger import get_processing_logger


logger = logging.getLogger(__name__)


class GmailClient:
    """Gmail API client for email operations."""
    
    def __init__(self, auth: GmailAuth):
        self.auth = auth
        self.settings = get_settings()
        self.service = None
        
    async def initialize(self) -> bool:
        """Initialize the Gmail service."""
        try:
            credentials = self.auth.get_credentials()
            if not credentials:
                logger.error("No valid Gmail credentials available")
                return False
            
            # Build the service
            self.service = build('gmail', 'v1', credentials=credentials)
            
            # Test the connection
            profile = self.service.users().getProfile(userId='me').execute()
            logger.info(f"Gmail service initialized for {profile.get('emailAddress')}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Gmail service: {e}")
            return False
    
    async def get_recent_emails_with_attachments(
        self,
        hours_back: int = 24,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Get recent emails with PDF attachments."""
        return await self._get_recent_emails(hours_back, max_results, require_attachments=True)
    
    async def get_all_recent_emails_with_pdfs(
        self,
        hours_back: int = 24,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Get ALL recent emails with PDF attachments (no domain/subject filtering)."""
        if not self.service:
            logger.error("Gmail service not initialized")
            return []
        
        try:
            # Calculate time range
            since_time = datetime.utcnow() - timedelta(hours=hours_back)
            
            # Simple query: just PDFs after a certain date (no domain filtering)
            query = f"has:attachment filename:pdf after:{since_time.strftime('%Y/%m/%d')}"
            
            logger.info(f"Searching for ALL emails with PDFs using query: {query}")
            
            # Search for emails
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            logger.info(f"Found {len(messages)} messages matching criteria")
            
            # Get detailed message information
            detailed_messages = []
            for message in messages:
                try:
                    detailed_msg = await self._get_message_details_flexible(message['id'], require_attachments=True)
                    if detailed_msg:
                        detailed_messages.append(detailed_msg)
                except Exception as e:
                    logger.warning(f"Failed to get details for message {message['id']}: {e}")
                    continue
            
            logger.info(f"Retrieved details for {len(detailed_messages)} messages")
            return detailed_messages
            
        except HttpError as e:
            logger.error(f"Gmail API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting emails: {e}")
            return []
    
    async def get_recent_emails_all(
        self,
        hours_back: int = 24,
        max_results: int = 100,
        sender_filter: str = None
    ) -> List[Dict[str, Any]]:
        """Get ALL recent emails (with or without attachments)."""
        return await self._get_recent_emails(hours_back, max_results, require_attachments=False, sender_filter=sender_filter)
    
    async def _get_recent_emails(
        self,
        hours_back: int = 24,
        max_results: int = 100,
        require_attachments: bool = True,
        sender_filter: str = None
    ) -> List[Dict[str, Any]]:
        """Internal method to get recent emails with flexible filtering."""
        if not self.service:
            logger.error("Gmail service not initialized")
            return []
        
        try:
            # Calculate time range
            since_time = datetime.utcnow() - timedelta(hours=hours_back)
            
            # Build query
            if sender_filter:
                query = f"from:{sender_filter} after:{since_time.strftime('%Y/%m/%d')}"
            else:
                query = self._build_search_query(since_time) if require_attachments else f"after:{since_time.strftime('%Y/%m/%d')}"
            
            logger.info(f"Searching for emails with query: {query}")
            
            # Search for emails
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            logger.info(f"Found {len(messages)} messages matching criteria")
            
            # Get detailed message information
            detailed_messages = []
            for message in messages:
                try:
                    detailed_msg = await self._get_message_details_flexible(message['id'], require_attachments)
                    if detailed_msg:
                        detailed_messages.append(detailed_msg)
                except Exception as e:
                    logger.warning(f"Failed to get details for message {message['id']}: {e}")
                    continue
            
            logger.info(f"Retrieved details for {len(detailed_messages)} messages")
            return detailed_messages
            
        except HttpError as e:
            logger.error(f"Gmail API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting emails: {e}")
            return []
    
    async def _get_message_details(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific message (requires attachments)."""
        return await self._get_message_details_flexible(message_id, require_attachments=True)
    
    async def _get_message_details_flexible(self, message_id: str, require_attachments: bool = True) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific message with flexible attachment requirements."""
        try:
            message = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()
            
            # Extract basic message info
            headers = {h['name']: h['value'] for h in message['payload'].get('headers', [])}
            
            message_info = {
                'id': message_id,
                'thread_id': message.get('threadId'),
                'subject': headers.get('Subject', ''),
                'sender': headers.get('From', ''),
                'date': headers.get('Date', ''),
                'timestamp': int(message.get('internalDate', 0)) / 1000,
                'attachments': [],
                'body': ''
            }

            # Extract email body
            body_text = self._extract_body(message['payload'])
            message_info['body'] = body_text

            # Extract attachments
            attachments = await self._extract_attachments(message['payload'], message_id)
            message_info['attachments'] = attachments
            
            # Handle PDF attachments
            pdf_attachments = [att for att in attachments if att['filename'].lower().endswith('.pdf')]
            if pdf_attachments:
                message_info['pdf_attachments'] = pdf_attachments
            
            # Return based on attachment requirement
            if require_attachments:
                # Only return messages with PDF attachments (old behavior)
                return message_info if pdf_attachments else None
            else:
                # Return ALL messages (new behavior)
                if not pdf_attachments:
                    message_info['pdf_attachments'] = []
                return message_info
            
        except Exception as e:
            logger.error(f"Failed to get message details for {message_id}: {e}")
            return None

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        """Extract email body text from message payload."""
        body = ""

        try:
            # Try to get plain text or HTML body
            if 'body' in payload and payload['body'].get('data'):
                # Single-part message
                body_data = payload['body']['data']
                body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
            elif 'parts' in payload:
                # Multi-part message
                for part in payload['parts']:
                    if part.get('mimeType') == 'text/plain':
                        if 'data' in part.get('body', {}):
                            body_data = part['body']['data']
                            body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                            break
                    elif part.get('mimeType') == 'text/html' and not body:
                        # Fall back to HTML if no plain text
                        if 'data' in part.get('body', {}):
                            body_data = part['body']['data']
                            body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                    elif 'parts' in part:
                        # Nested parts (e.g., multipart/alternative)
                        for subpart in part['parts']:
                            if subpart.get('mimeType') == 'text/plain':
                                if 'data' in subpart.get('body', {}):
                                    body_data = subpart['body']['data']
                                    body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')
                                    break
                            elif subpart.get('mimeType') == 'text/html' and not body:
                                if 'data' in subpart.get('body', {}):
                                    body_data = subpart['body']['data']
                                    body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')

        except Exception as e:
            logger.warning(f"Failed to extract body: {e}")

        return body.strip()

    async def _extract_attachments(
        self,
        payload: Dict[str, Any],
        message_id: str
    ) -> List[Dict[str, Any]]:
        """Extract attachment information from message payload."""
        attachments = []
        
        def extract_parts(part):
            if 'parts' in part:
                for subpart in part['parts']:
                    extract_parts(subpart)
            else:
                if part.get('filename'):
                    body = part.get('body', {})
                    if 'attachmentId' in body:
                        attachments.append({
                            'filename': part['filename'],
                            'mime_type': part.get('mimeType', ''),
                            'size': body.get('size', 0),
                            'attachment_id': body['attachmentId']
                        })
        
        extract_parts(payload)
        return attachments
    
    async def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        filename: str
    ) -> Optional[bytes]:
        """Download an email attachment."""
        try:
            attachment = self.service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()
            
            data = attachment['data']
            # Decode base64url data
            file_data = base64.urlsafe_b64decode(data.encode('UTF-8'))
            
            logger.info(f"Downloaded attachment {filename} ({len(file_data)} bytes)")
            return file_data
            
        except Exception as e:
            logger.error(f"Failed to download attachment {filename}: {e}")
            return None
    
    async def mark_message_as_processed(self, message_id: str) -> bool:
        """Mark a message as processed by adding a label."""
        try:
            # Add a custom label or modify message
            # For now, just mark as read
            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            
            logger.info(f"Marked message {message_id} as processed")
            return True
            
        except Exception as e:
            logger.error(f"Failed to mark message {message_id} as processed: {e}")
            return False
    
    def _build_search_query(self, since_time: datetime) -> str:
        """Build Gmail search query based on configuration."""
        query_parts = []
        
        # Has attachment
        query_parts.append("has:attachment")
        
        # PDF files
        query_parts.append("filename:pdf")
        
        # Time range
        date_str = since_time.strftime("%Y/%m/%d")
        query_parts.append(f"after:{date_str}")
        
        # Combine domain and subject filters with OR logic
        # This way we get emails that match EITHER domain OR subject criteria
        domain_and_subject_queries = []
        
        # Add domain queries
        if self.settings.sender_domains_list:
            for domain in self.settings.sender_domains_list:
                domain_and_subject_queries.append(f"from:{domain}")
        
        # Add subject keyword queries
        if self.settings.subject_keywords_list:
            for keyword in self.settings.subject_keywords_list:
                # Add original keyword
                domain_and_subject_queries.append(f"subject:{keyword}")
                # Add lowercase version
                if keyword.lower() != keyword:
                    domain_and_subject_queries.append(f"subject:{keyword.lower()}")
                # Add title case version
                if keyword.capitalize() != keyword:
                    domain_and_subject_queries.append(f"subject:{keyword.capitalize()}")
        
        # Combine all domain and subject queries with OR
        if domain_and_subject_queries:
            if len(domain_and_subject_queries) == 1:
                query_parts.append(domain_and_subject_queries[0])
            else:
                query_parts.append(f"({' OR '.join(domain_and_subject_queries)})")
        
        query = " ".join(query_parts)
        return query
    
    async def get_message_by_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific message by ID."""
        if not self.service:
            logger.error("Gmail service not initialized")
            return None
        
        return await self._get_message_details(message_id)
    
    async def test_connection(self) -> bool:
        """Test Gmail API connection."""
        try:
            if not self.service:
                initialized = await self.initialize()
                if not initialized:
                    return False
            
            # Simple test query
            profile = self.service.users().getProfile(userId='me').execute()
            logger.info(f"Gmail connection test successful for {profile.get('emailAddress')}")
            return True
            
        except Exception as e:
            logger.error(f"Gmail connection test failed: {e}")
            return False
    
    async def get_processing_stats(self) -> Dict[str, Any]:
        """Get processing statistics from Gmail."""
        try:
            if not self.service:
                return {"error": "Service not initialized"}
            
            # Get recent message count
            recent_query = self._build_search_query(datetime.utcnow() - timedelta(days=7))
            recent_results = self.service.users().messages().list(
                userId='me',
                q=recent_query,
                maxResults=1000
            ).execute()
            
            recent_count = len(recent_results.get('messages', []))
            
            return {
                "recent_messages_7_days": recent_count,
                "last_checked": datetime.utcnow().isoformat(),
                "search_query": recent_query
            }
            
        except Exception as e:
            logger.error(f"Failed to get Gmail processing stats: {e}")
            return {"error": str(e)}


async def create_gmail_client() -> Optional[GmailClient]:
    """Create and initialize Gmail client."""
    try:
        auth = GmailAuth()
        client = GmailClient(auth)
        
        initialized = await client.initialize()
        if not initialized:
            logger.error("Failed to initialize Gmail client")
            return None
        
        logger.info("Gmail client created and initialized successfully")
        return client
        
    except Exception as e:
        logger.error(f"Failed to create Gmail client: {e}")
        return None