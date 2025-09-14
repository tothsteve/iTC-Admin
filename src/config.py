"""Configuration management for ITC-Admin Gmail automation system."""

import os
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Settings(BaseSettings):
    """Application configuration settings."""
    
    # Gmail API Configuration
    gmail_client_id: str = Field(..., env="GMAIL_CLIENT_ID")
    gmail_client_secret: str = Field(..., env="GMAIL_CLIENT_SECRET")
    gmail_redirect_uri: str = Field(default="http://localhost:8080/oauth2callback", env="GMAIL_REDIRECT_URI")
    
    # Dropbox Local Sync Folder
    dropbox_sync_folder: str = Field(default="/Users/tothi/Dropbox/ITC-Admin-Invoices", env="DROPBOX_SYNC_FOLDER")
    
    # Google Sheets API Configuration
    sheets_client_id: str = Field(..., env="SHEETS_CLIENT_ID")
    sheets_client_secret: str = Field(..., env="SHEETS_CLIENT_SECRET")
    sheets_spreadsheet_id: str = Field(..., env="SHEETS_SPREADSHEET_ID")
    
    # TransferXMLGenerator Integration
    transfer_api_url: str = Field(default="http://localhost:8000", env="TRANSFER_API_URL")
    transfer_api_token: Optional[str] = Field(default=None, env="TRANSFER_API_TOKEN")
    
    # Database Configuration
    database_url: str = Field(default="sqlite:///data/database/invoices.db", env="DATABASE_URL")
    
    # Processing Configuration
    max_concurrent_processes: int = Field(default=5, env="MAX_CONCURRENT_PROCESSES")
    retry_max_attempts: int = Field(default=3, env="RETRY_MAX_ATTEMPTS")
    retry_backoff_seconds: int = Field(default=30, env="RETRY_BACKOFF_SECONDS")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_file: str = Field(default="logs/itc_admin.log", env="LOG_FILE")
    log_max_size: str = Field(default="10MB", env="LOG_MAX_SIZE")
    log_backup_count: int = Field(default=5, env="LOG_BACKUP_COUNT")
    
    # Gmail Monitoring Configuration
    gmail_sender_domains: str = Field(default="nav.gov.hu,partner-company.hu", env="GMAIL_SENDER_DOMAINS")
    gmail_subject_keywords: str = Field(default="számla,invoice,NAV", env="GMAIL_SUBJECT_KEYWORDS")
    gmail_max_file_size_mb: int = Field(default=50, env="GMAIL_MAX_FILE_SIZE_MB")
    
    @property
    def sender_domains_list(self) -> List[str]:
        """Return sender domains as a list."""
        return [domain.strip() for domain in self.gmail_sender_domains.split(",")]
    
    @property
    def subject_keywords_list(self) -> List[str]:
        """Return subject keywords as a list."""
        return [keyword.strip() for keyword in self.gmail_subject_keywords.split(",")]
    
    @property
    def credentials_dir(self) -> str:
        """Return credentials directory path."""
        return os.path.join(os.getcwd(), "data", "credentials")
    
    @property
    def database_dir(self) -> str:
        """Return database directory path."""
        return os.path.join(os.getcwd(), "data", "database")
    
    @property
    def temp_dir(self) -> str:
        """Return temporary directory path."""
        return os.path.join(os.getcwd(), "data", "temp")
    
    @property
    def log_dir(self) -> str:
        """Return logs directory path."""
        return os.path.join(os.getcwd(), "logs")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings instance."""
    return settings