"""Configuration management for ITC-Admin Gmail automation system."""

import os
from typing import Optional
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
    
    # Processing Configuration
    max_concurrent_processes: int = Field(default=5, env="MAX_CONCURRENT_PROCESSES")
    retry_max_attempts: int = Field(default=3, env="RETRY_MAX_ATTEMPTS")
    retry_backoff_seconds: int = Field(default=30, env="RETRY_BACKOFF_SECONDS")

    # Logging Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_dir: str = Field(default="logs", env="LOG_DIR")
    log_file: str = Field(default="logs/itc_admin.log", env="LOG_FILE")
    log_max_size: str = Field(default="10MB", env="LOG_MAX_SIZE")
    log_backup_count: int = Field(default=5, env="LOG_BACKUP_COUNT")

    @property
    def credentials_dir(self) -> str:
        """Return credentials directory path."""
        return os.path.join(os.getcwd(), "data", "credentials")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get application settings instance."""
    return settings