"""Gmail OAuth2 authentication module."""

import json
import os
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from config import get_settings


logger = logging.getLogger(__name__)


class GmailAuth:
    """Handles Gmail OAuth2 authentication and credential management."""
    
    def __init__(self):
        self.settings = get_settings()
        self.credentials_file = os.path.join(self.settings.credentials_dir, "gmail_credentials.json")
        self.token_file = os.path.join(self.settings.credentials_dir, "gmail_token.json")
        
        # Gmail API scopes
        self.scopes = [
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.modify'
        ]
        
    def get_credentials(self) -> Optional[Credentials]:
        """Get valid Gmail API credentials."""
        creds = None
        
        # Load existing token if available
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, self.scopes)
                logger.info("Loaded existing Gmail credentials from token file")
            except Exception as e:
                logger.warning(f"Failed to load existing credentials: {e}")
        
        # If credentials are invalid or don't exist, refresh or get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired Gmail credentials")
                    creds.refresh(Request())
                    logger.info("Successfully refreshed Gmail credentials")
                except Exception as e:
                    logger.error(f"Failed to refresh credentials: {e}")
                    creds = None
            
            if not creds:
                logger.info("No valid credentials found, initiating OAuth flow")
                creds = self._run_oauth_flow()
        
        # Save credentials for future use
        if creds:
            self._save_credentials(creds)
            logger.info("Gmail credentials are ready")
        
        return creds
    
    def _run_oauth_flow(self) -> Optional[Credentials]:
        """Run the OAuth2 flow to get new credentials."""
        try:
            # Create client configuration
            client_config = {
                "web": {
                    "client_id": self.settings.gmail_client_id,
                    "client_secret": self.settings.gmail_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.settings.gmail_redirect_uri]
                }
            }
            
            # Create flow
            flow = Flow.from_client_config(
                client_config,
                scopes=self.scopes
            )
            flow.redirect_uri = self.settings.gmail_redirect_uri
            
            # Get authorization URL (without include_granted_scopes to avoid scope conflicts)
            auth_url, _ = flow.authorization_url(
                access_type='offline'
            )
            
            logger.info(f"Please visit this URL to authorize the application: {auth_url}")
            print(f"\\nPlease visit this URL to authorize the Gmail integration:")
            print(f"{auth_url}\\n")
            
            # Get authorization code from user
            authorization_code = input("Enter the authorization code: ").strip()
            
            if not authorization_code:
                logger.error("No authorization code provided")
                return None
            
            # Exchange code for credentials
            flow.fetch_token(code=authorization_code)
            creds = flow.credentials
            
            logger.info("Successfully obtained Gmail credentials via OAuth flow")
            return creds
            
        except Exception as e:
            logger.error(f"OAuth flow failed: {e}")
            return None
    
    def _save_credentials(self, creds: Credentials) -> None:
        """Save credentials to file."""
        try:
            # Ensure credentials directory exists
            Path(self.settings.credentials_dir).mkdir(parents=True, exist_ok=True)
            
            # Save credentials
            with open(self.token_file, 'w') as token:
                token.write(creds.to_json())
            
            logger.info(f"Saved Gmail credentials to {self.token_file}")
            
        except Exception as e:
            logger.error(f"Failed to save credentials: {e}")
    
    def revoke_credentials(self) -> bool:
        """Revoke and delete stored credentials."""
        try:
            # Load existing credentials
            creds = self.get_credentials()
            if creds:
                # Revoke the credentials
                creds.revoke(Request())
                logger.info("Successfully revoked Gmail credentials")
            
            # Delete token file
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
                logger.info("Deleted stored Gmail credentials")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to revoke credentials: {e}")
            return False
    
    def check_credentials_status(self) -> dict:
        """Check the status of stored credentials."""
        status = {
            "has_credentials": False,
            "is_valid": False,
            "is_expired": False,
            "has_refresh_token": False,
            "scopes": []
        }
        
        try:
            creds = self.get_credentials()
            if creds:
                status["has_credentials"] = True
                status["is_valid"] = creds.valid
                status["is_expired"] = creds.expired if hasattr(creds, 'expired') else False
                status["has_refresh_token"] = bool(creds.refresh_token)
                status["scopes"] = list(creds.scopes) if hasattr(creds, 'scopes') else []
                
        except Exception as e:
            logger.error(f"Failed to check credentials status: {e}")
        
        return status


def setup_gmail_auth() -> GmailAuth:
    """Setup and return Gmail authentication instance."""
    auth = GmailAuth()
    
    # Check if credentials are already available
    status = auth.check_credentials_status()
    if status["has_credentials"] and status["is_valid"]:
        logger.info("Gmail authentication is already configured and valid")
    else:
        logger.info("Gmail authentication needs to be configured")
        auth.get_credentials()
    
    return auth