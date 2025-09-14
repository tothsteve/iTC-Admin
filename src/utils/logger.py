"""Logging configuration for ITC-Admin system."""

import logging
import logging.handlers
import structlog
from pathlib import Path
from typing import Any, Dict

from config import Settings


def setup_logging(settings: Settings) -> None:
    """Setup structured logging configuration."""
    
    # Ensure logs directory exists
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure standard library logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(message)s",
        handlers=[
            # Console handler
            logging.StreamHandler(),
            # Rotating file handler
            logging.handlers.RotatingFileHandler(
                filename=settings.log_file,
                maxBytes=_parse_size(settings.log_max_size),
                backupCount=settings.log_backup_count,
                encoding='utf-8'
            )
        ]
    )
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str, **context: Any) -> structlog.BoundLogger:
    """Get a structured logger with optional context."""
    logger = structlog.get_logger(name)
    if context:
        logger = logger.bind(**context)
    return logger


def _parse_size(size_str: str) -> int:
    """Parse size string (e.g., '10MB') to bytes."""
    size_str = size_str.upper().strip()
    
    if size_str.endswith('KB'):
        return int(size_str[:-2]) * 1024
    elif size_str.endswith('MB'):
        return int(size_str[:-2]) * 1024 * 1024
    elif size_str.endswith('GB'):
        return int(size_str[:-2]) * 1024 * 1024 * 1024
    else:
        # Assume bytes
        return int(size_str)


class ProcessingLogger:
    """Specialized logger for processing operations with correlation IDs."""
    
    def __init__(self, base_logger: structlog.BoundLogger, correlation_id: str):
        self.logger = base_logger.bind(correlation_id=correlation_id)
        self.correlation_id = correlation_id
    
    def info(self, message: str, **kwargs):
        """Log info message with correlation ID."""
        self.logger.info(message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message with correlation ID."""
        self.logger.warning(message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message with correlation ID."""
        self.logger.error(message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """Log debug message with correlation ID."""
        self.logger.debug(message, **kwargs)


def get_processing_logger(operation: str, correlation_id: str) -> ProcessingLogger:
    """Get a processing logger with correlation ID context."""
    base_logger = get_logger(f"processing.{operation}")
    return ProcessingLogger(base_logger, correlation_id)