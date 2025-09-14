"""Local Dropbox folder manager - copies files to synced Dropbox folder."""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from config import get_settings


logger = logging.getLogger(__name__)


class LocalDropboxManager:
    """Manages copying files to local synced Dropbox folder."""
    
    def __init__(self):
        self.settings = get_settings()
        self.dropbox_folder = Path(self.settings.dropbox_sync_folder)
        
    async def initialize(self) -> bool:
        """Initialize local Dropbox folder."""
        try:
            logger.info(f"Initializing local Dropbox folder: {self.dropbox_folder}")
            
            # Create main folder if it doesn't exist
            self.dropbox_folder.mkdir(parents=True, exist_ok=True)
            
            # Test write access
            test_file = self.dropbox_folder / ".test_write"
            try:
                test_file.write_text("test")
                test_file.unlink()
                logger.info("✅ Dropbox folder is writable")
            except Exception as e:
                logger.error(f"❌ Cannot write to Dropbox folder: {e}")
                return False
            
            logger.info("Local Dropbox manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize local Dropbox manager: {e}")
            return False
    
    async def copy_pdf(
        self, 
        local_file_path: Path, 
        email_data: Dict[str, Any]
    ) -> Optional[str]:
        """
        Copy PDF to local Dropbox folder and return the path.
        
        Args:
            local_file_path: Path to local PDF file
            email_data: Email metadata for organizing files
            
        Returns:
            Relative path to the copied file or None if failed
        """
        try:
            # Copy directly to the Dropbox sync folder (no subfolders)
            target_file_path = self.dropbox_folder / local_file_path.name
            
            # Handle duplicate filenames
            counter = 1
            original_target = target_file_path
            while target_file_path.exists():
                name_parts = original_target.stem, counter, original_target.suffix
                target_file_path = original_target.parent / f"{name_parts[0]}_{name_parts[1]}{name_parts[2]}"
                counter += 1
            
            logger.info(f"Copying {local_file_path.name} to Dropbox: {target_file_path}")
            
            # Copy the file
            shutil.copy2(local_file_path, target_file_path)
            
            # Verify the copy
            if target_file_path.exists() and target_file_path.stat().st_size > 0:
                # Return just the filename since it's directly in the sync folder
                logger.info(f"✅ Successfully copied to Dropbox: {target_file_path.name}")
                
                # Return the full path for the Google Sheets link
                return str(target_file_path)
            else:
                logger.error(f"❌ Copy verification failed for {target_file_path}")
                return None
            
        except Exception as e:
            logger.error(f"Failed to copy PDF to Dropbox: {e}")
            return None
    
    def _sanitize_folder_name(self, name: str) -> str:
        """Sanitize email address for use as folder name."""
        # Remove invalid characters for folder names
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
        sanitized = name
        
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Replace @ with _at_ for email addresses
        sanitized = sanitized.replace('@', '_at_')
        
        # Limit length
        if len(sanitized) > 50:
            sanitized = sanitized[:50]
        
        return sanitized
    
    async def test_connection(self) -> bool:
        """Test Dropbox folder access."""
        try:
            if not self.dropbox_folder.exists():
                logger.error(f"Dropbox folder does not exist: {self.dropbox_folder}")
                return False
            
            if not self.dropbox_folder.is_dir():
                logger.error(f"Dropbox path is not a directory: {self.dropbox_folder}")
                return False
            
            # Test write access
            test_file = self.dropbox_folder / f".test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                test_file.write_text("connection test")
                test_file.unlink()
                logger.info(f"✅ Dropbox folder access test successful: {self.dropbox_folder}")
                return True
            except Exception as e:
                logger.error(f"❌ Cannot write to Dropbox folder: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Dropbox connection test failed: {e}")
            return False
    
    async def get_folder_stats(self) -> Dict[str, Any]:
        """Get folder statistics."""
        try:
            if not self.dropbox_folder.exists():
                return {
                    "folder_exists": False,
                    "error": "Dropbox folder does not exist"
                }
            
            # Count files and calculate total size
            total_files = 0
            total_size = 0
            pdf_files = 0
            
            for file_path in self.dropbox_folder.rglob('*'):
                if file_path.is_file():
                    total_files += 1
                    total_size += file_path.stat().st_size
                    
                    if file_path.suffix.lower() == '.pdf':
                        pdf_files += 1
            
            return {
                "folder_exists": True,
                "folder_path": str(self.dropbox_folder),
                "total_files": total_files,
                "pdf_files": pdf_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024*1024), 2)
            }
            
        except Exception as e:
            logger.error(f"Failed to get folder stats: {e}")
            return {"error": str(e)}
    
    async def create_folder_structure(self) -> bool:
        """Create initial folder structure."""
        try:
            # Create main folder
            self.dropbox_folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created main folder: {self.dropbox_folder}")
            
            # Create monthly subfolder
            current_month = datetime.now().strftime('%Y-%m')
            monthly_folder = self.dropbox_folder / current_month
            monthly_folder.mkdir(exist_ok=True)
            logger.info(f"Created monthly folder: {monthly_folder}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create folder structure: {e}")
            return False


async def create_local_dropbox_manager() -> Optional[LocalDropboxManager]:
    """Create and initialize local Dropbox manager."""
    try:
        manager = LocalDropboxManager()
        initialized = await manager.initialize()
        
        if not initialized:
            logger.error("Failed to initialize local Dropbox manager")
            return None
        
        # Create folder structure
        await manager.create_folder_structure()
        
        logger.info("Local Dropbox manager created and initialized successfully")
        return manager
        
    except Exception as e:
        logger.error(f"Failed to create local Dropbox manager: {e}")
        return None