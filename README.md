# ITC-Admin - Gmail Invoice Automation System

A Python-based automation system that processes Gmail emails with PDF invoice attachments, uploads them to Dropbox, extracts invoice data using OCR, updates Google Sheets for tracking, and integrates with the existing transferXMLGenerator for payment processing.

## Features

- **Gmail Integration**: Monitor Gmail for new emails with PDF attachments
- **PDF Processing**: Extract text using PyPDF2 with OCR fallback (Tesseract)
- **Dropbox Integration**: Upload processed PDFs and generate shareable links
- **Google Sheets Integration**: Track invoices, payments, and processing status
- **TransferXMLGenerator Integration**: Send invoice data to existing Django system
- **State Management**: SQLite database for processing state and retry logic
- **Error Handling**: Comprehensive logging and failure recovery

## Quick Start

### Prerequisites

- Python 3.9+
- Docker and Docker Compose (optional)
- Gmail API credentials
- Dropbox API credentials
- Google Sheets API credentials

### Installation

1. **Clone the repository:**
   ```bash
   git clone <repo_url> ITC-Admin
   cd ITC-Admin
   ```

2. **Set up Python virtual environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # or
   venv\Scripts\activate     # Windows
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env with your API credentials
   ```

5. **Setup OAuth2 credentials:**
   ```bash
   python scripts/setup_auth.py
   ```

6. **Initialize database:**
   ```bash
   python src/database/migrations.py
   ```

### Running the Application

**Development Mode:**
```bash
python src/main.py
```

**Production Mode (Docker):**
```bash
docker-compose up -d
```

**View logs:**
```bash
# Development
tail -f logs/itc_admin.log

# Docker
docker-compose logs -f itc-admin
```

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure the following:

**Gmail API:**
- `GMAIL_CLIENT_ID`: Your Gmail API client ID
- `GMAIL_CLIENT_SECRET`: Your Gmail API client secret
- `GMAIL_REDIRECT_URI`: OAuth redirect URI (default: http://localhost:8080/oauth2callback)

**Dropbox API:**
- `DROPBOX_ACCESS_TOKEN`: Your Dropbox access token
- `DROPBOX_APP_KEY`: Your Dropbox app key (optional)
- `DROPBOX_APP_SECRET`: Your Dropbox app secret (optional)

**Google Sheets API:**
- `SHEETS_CLIENT_ID`: Your Google Sheets API client ID
- `SHEETS_CLIENT_SECRET`: Your Google Sheets API client secret
- `SHEETS_SPREADSHEET_ID`: Target Google Sheets spreadsheet ID

**TransferXMLGenerator Integration:**
- `TRANSFER_API_URL`: URL to your transferXMLGenerator API (default: http://localhost:8000)
- `TRANSFER_API_TOKEN`: JWT token for API authentication

**Processing Configuration:**
- `MAX_CONCURRENT_PROCESSES`: Maximum concurrent processes (default: 5)
- `RETRY_MAX_ATTEMPTS`: Maximum retry attempts for failed processing (default: 3)
- `RETRY_BACKOFF_SECONDS`: Seconds to wait between retries (default: 30)

### Gmail Monitoring Configuration

Configure which emails to process:
- `GMAIL_SENDER_DOMAINS`: Comma-separated sender domains (e.g., nav.gov.hu,partner-company.hu)
- `GMAIL_SUBJECT_KEYWORDS`: Comma-separated keywords to match in subject (e.g., számla,invoice,NAV)
- `GMAIL_MAX_FILE_SIZE_MB`: Maximum PDF file size in MB (default: 50)

## Development

### Project Structure

```
ITC-Admin/
├── src/                    # Main application code
│   ├── gmail/             # Gmail API integration
│   ├── pdf/               # PDF processing
│   ├── dropbox/           # Dropbox integration
│   ├── sheets/            # Google Sheets integration
│   ├── transfer/          # TransferXMLGenerator integration
│   ├── database/          # State management
│   └── utils/             # Shared utilities
├── tests/                 # Test suites
├── scripts/               # Utility scripts
├── logs/                  # Application logs
└── data/                  # Local data storage
```

For detailed documentation, see [CLAUDE.md](CLAUDE.md).