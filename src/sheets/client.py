"""Google Sheets API client for logging email processing data."""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from config import get_settings


logger = logging.getLogger(__name__)


class SheetsClient:
    """Google Sheets API client for logging invoice processing."""
    
    def __init__(self):
        self.settings = get_settings()
        self.client = None
        self.worksheet = None
        
        # Define the spreadsheet columns
        self.headers = [
            'Processing Date',
            'Gmail Message ID', 
            'Sender Email',
            'Subject',
            'PDF Filename',
            'PDF Size (MB)',
            'Local Path',
            'Dropbox Link',
            'Processing Status',
            'Error Message',
            'Timestamp'
        ]
        
    async def initialize(self) -> bool:
        """Initialize Google Sheets connection."""
        try:
            logger.info("Initializing Google Sheets client...")
            
            # Use simple gspread OAuth (same as working test file)
            try:
                self.client = gspread.oauth()
                logger.info("✅ Connected to Google Sheets using OAuth")
            except Exception as e:
                logger.error(f"Failed to connect to Google Sheets: {e}")
                return False
            
            # Open the spreadsheet
            try:
                spreadsheet = self.client.open_by_key(self.settings.sheets_spreadsheet_id)
                logger.info(f"Opened spreadsheet: {spreadsheet.title}")
            except gspread.SpreadsheetNotFound:
                logger.error(f"Spreadsheet not found with ID: {self.settings.sheets_spreadsheet_id}")
                return False
            
            # Use existing 2026 worksheet (user confirmed it exists with data)
            worksheet_name = "2026"
            try:
                self.worksheet = spreadsheet.worksheet(worksheet_name)
                logger.info(f"Connected to existing worksheet: {worksheet_name}")
                logger.info(f"Worksheet dimensions: {self.worksheet.row_count} rows × {self.worksheet.col_count} cols")
                
                # Get existing structure - don't modify existing headers
                existing_headers = self.worksheet.row_values(1) if self.worksheet.row_count > 0 else []
                logger.info(f"Existing worksheet headers: {existing_headers}")

                # NEW: Ensure duplicate prevention headers are added if missing
                await self._ensure_headers_exist(existing_headers)

                # We'll append our invoice processing data to the existing structure
                # Find the next available row for appending
                all_values = self.worksheet.get_all_values()
                non_empty_rows = len([row for row in all_values if any(cell.strip() for cell in row)])
                self.next_row = non_empty_rows + 1
                logger.info(f"Next available row for appending: {self.next_row}")
                
            except gspread.WorksheetNotFound:
                logger.error(f"Worksheet '{worksheet_name}' not found!")
                logger.error("User indicated this worksheet exists with data.")
                return False
            
            logger.info("Google Sheets client initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets client: {e}")
            return False
    
    def _get_credentials(self) -> Optional[UserCredentials]:
        """Get Google Sheets credentials."""
        credentials_file = Path(self.settings.credentials_dir) / "sheets_credentials.json"
        token_file = Path(self.settings.credentials_dir) / "sheets_token.json"
        
        creds = None
        
        # Load existing token if available
        if token_file.exists():
            try:
                creds = UserCredentials.from_authorized_user_file(
                    str(token_file),
                    ['https://www.googleapis.com/auth/spreadsheets']
                )
                logger.info("Loaded existing Sheets credentials")
            except Exception as e:
                logger.warning(f"Failed to load existing credentials: {e}")
        
        # Refresh or get new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired Sheets credentials")
                    creds.refresh(Request())
                    logger.info("Successfully refreshed Sheets credentials")
                except Exception as e:
                    logger.error(f"Failed to refresh credentials: {e}")
                    creds = None
            
            if not creds:
                logger.info("Running OAuth flow for Sheets credentials")
                creds = self._run_oauth_flow()
        
        # Save credentials
        if creds and creds.valid:
            try:
                Path(self.settings.credentials_dir).mkdir(parents=True, exist_ok=True)
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
                logger.info("Saved Sheets credentials")
            except Exception as e:
                logger.error(f"Failed to save credentials: {e}")
        
        return creds
    
    def _run_oauth_flow(self) -> Optional[UserCredentials]:
        """Run OAuth flow for Google Sheets."""
        try:
            # Create client configuration
            client_config = {
                "web": {
                    "client_id": self.settings.sheets_client_id,
                    "client_secret": self.settings.sheets_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8080/oauth2callback"]
                }
            }
            
            # Create flow
            flow = Flow.from_client_config(
                client_config,
                scopes=['https://www.googleapis.com/auth/spreadsheets']
            )
            flow.redirect_uri = "http://localhost:8080/oauth2callback"
            
            # Get authorization URL
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true'
            )
            
            print(f"\nPlease visit this URL to authorize Google Sheets access:")
            print(f"{auth_url}\n")
            
            # Get authorization code from user
            authorization_code = input("Enter the authorization code: ").strip()
            
            if not authorization_code:
                logger.error("No authorization code provided")
                return None
            
            # Exchange code for credentials
            flow.fetch_token(code=authorization_code)
            creds = flow.credentials
            
            logger.info("Successfully obtained Sheets credentials via OAuth flow")
            return creds
            
        except Exception as e:
            logger.error(f"OAuth flow failed: {e}")
            return None

    async def _ensure_headers_exist(self, existing_headers: List[str]) -> bool:
        """Ensure the new duplicate prevention headers exist in the sheet."""
        try:
            # Define the expected headers for duplicate prevention
            expected_new_headers = [
                "Gmail Message ID",      # Column J
                "Verification Status",   # Column K
                "Verification Date",     # Column L
                "Processing Notes"       # Column M
            ]

            # Check if we need to add headers by looking for existing new headers
            headers_to_add = []

            # Check if each expected header already exists in the sheet
            for new_header in expected_new_headers:
                if new_header not in existing_headers:
                    headers_to_add.append(new_header)

            if headers_to_add:
                logger.info(f"Adding new headers to sheet: {headers_to_add}")

                # Add headers to row 1, starting from the next available column
                # Find the next empty column to avoid overwriting
                next_col = len([h for h in existing_headers if h.strip()]) + 1  # 1-based indexing
                for i, header in enumerate(headers_to_add):
                    col_letter = chr(ord('A') + next_col - 1 + i)  # Convert to A, B, C...
                    cell_range = f"{col_letter}1"
                    self.worksheet.update(cell_range, header)
                    logger.info(f"Added header '{header}' to column {col_letter}")

                logger.info("✅ Successfully added duplicate prevention headers")
            else:
                logger.info("Headers already exist, no additions needed")

            return True

        except Exception as e:
            logger.error(f"Failed to ensure headers exist: {e}")
            return False

    async def is_email_already_processed(self, gmail_message_id: str) -> Dict[str, Any]:
        """Check if email is already processed by looking up Gmail Message ID in the sheet."""
        if not self.worksheet or not gmail_message_id:
            return {"processed": False, "reason": "No worksheet or message ID"}

        try:
            logger.info(f"🔍 Checking if email {gmail_message_id} is already processed...")

            # Try to find the Gmail Message ID in column J (index 9)
            try:
                cell = self.worksheet.find(gmail_message_id)
                if cell is None:
                    logger.info(f"📧 Gmail Message ID {gmail_message_id} not found - email not yet processed")
                    return {"processed": False, "reason": "Message ID not found"}
                logger.info(f"📧 Found Gmail Message ID at row {cell.row}")

                # Get the row data to extract verification status and other info
                row_data = self.worksheet.row_values(cell.row)

                # Extract information from the row
                result = {
                    "processed": True,
                    "row_number": cell.row,
                    "processing_date": row_data[0] if len(row_data) > 0 else "",
                    "payment_type": row_data[1] if len(row_data) > 1 else "",
                    "amount": row_data[3] if len(row_data) > 3 else "",  # Kiadás HUF
                    "eur_amount": row_data[5] if len(row_data) > 5 else "",  # Kiadás EUR
                    "description": row_data[6] if len(row_data) > 6 else "",
                    "file_link": row_data[7] if len(row_data) > 7 else "",
                    "gmail_message_id": row_data[9] if len(row_data) > 9 else "",
                    "verification_status": row_data[10] if len(row_data) > 10 else "pending",
                    "verification_date": row_data[11] if len(row_data) > 11 else "",
                    "processing_notes": row_data[12] if len(row_data) > 12 else ""
                }

                logger.info(f"✅ Email already processed with verification status: {result['verification_status']}")
                return result

            except Exception as cell_not_found_error:
                # Handle cell not found (gspread exception naming can vary)
                if "not found" in str(cell_not_found_error).lower():
                    logger.info(f"📧 Gmail Message ID {gmail_message_id} not found - email not yet processed")
                    return {"processed": False, "reason": "Message ID not found"}
                else:
                    # Re-raise if it's not a "not found" error
                    raise cell_not_found_error

        except Exception as e:
            logger.error(f"Failed to check email processing status: {e}")
            return {"processed": False, "reason": f"Error: {e}"}

    async def should_reprocess_email(self, processing_info: Dict[str, Any]) -> Dict[str, Any]:
        """Determine if an already-processed email should be reprocessed based on verification status."""
        if not processing_info.get("processed", False):
            return {"should_reprocess": True, "reason": "Email not yet processed"}

        verification_status = processing_info.get("verification_status", "pending")

        if verification_status == "verified":
            return {
                "should_reprocess": False,
                "reason": f"Email already verified on {processing_info.get('verification_date', 'unknown date')}"
            }

        elif verification_status == "rejected":
            return {
                "should_reprocess": True,
                "reason": f"Email marked for reprocessing (rejected)"
            }

        else:  # pending or unknown status
            return {
                "should_reprocess": False,
                "reason": f"Email with status '{verification_status}' since {processing_info.get('processing_date', 'unknown')}"
            }

    async def log_email_processing(self, email_data: Dict[str, Any]) -> bool:
        """Log email processing data to existing 2025 worksheet."""
        if not self.worksheet:
            logger.error("Sheets client not initialized")
            return False
        
        try:
            # Prepare invoice processing data to append to existing sheet structure
            # We'll add our data as additional columns or use existing columns that match
            
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Create row data matching the existing 2025 sheet structure:
            # Columns: Dátum, Fizetve, Bevétel HUF, Kiadás HUF, Bevétel EUR, Kiadás EUR, Megjegyzés, Link a számlára, Column2
            
            # Use extracted amount as integer (no comma formatting, no quotes)
            extracted_amount = email_data.get('extracted_amount')
            amount_value = int(extracted_amount) if extracted_amount else ""
            
            # Use extracted EUR amount as float to preserve decimals (32.4 not 32)
            extracted_eur_amount = email_data.get('extracted_eur_amount')
            eur_amount_value = extracted_eur_amount if extracted_eur_amount else ""

            # Use extracted USD amount as float to preserve decimals
            extracted_usd_amount = email_data.get('extracted_usd_amount')
            usd_amount_value = extracted_usd_amount if extracted_usd_amount else ""

            # Use extracted due date as proper date object for column A
            due_date = email_data.get('due_date')
            if due_date and len(due_date) == 8:  # YYYYMMDD format
                # Create a proper date object to avoid quote formatting in Google Sheets
                try:
                    date_obj = datetime.strptime(due_date, '%Y%m%d')
                    date_value = date_obj.strftime('%Y-%m-%d')
                except:
                    date_value = datetime.now().strftime('%Y-%m-%d')
            else:
                date_value = datetime.now().strftime('%Y-%m-%d')
            
            # Use sheet description from classification/rules if available
            sheet_description = email_data.get('sheet_description', f"Email: {email_data.get('pdf_filename', 'unknown')} from {email_data.get('sender_email', 'unknown')}")

            # Use EUR column for USD amounts (Column F)
            # If both EUR and USD are present, prioritize EUR and add USD to description
            eur_column_value = ""
            if eur_amount_value:
                eur_column_value = eur_amount_value
                if usd_amount_value:
                    sheet_description = f"{sheet_description} (${usd_amount_value:.2f} USD)"
            elif usd_amount_value:
                # Use USD in EUR column when no EUR amount
                eur_column_value = usd_amount_value

            # Use payment type from classification (either "Vállalati számla" or "Saját")
            payment_type = email_data.get('payment_type', 'Vállalati számla')
            
            # NEW: Add duplicate prevention tracking data
            gmail_message_id = email_data.get('gmail_message_id', '')
            # Mark as verified if processing was successful, pending only if there were issues
            verification_status = email_data.get('verification_status', 'verified')  # Default to verified for successful processing
            verification_date = email_data.get('verification_date', current_time)  # Set verification date to now
            processing_notes = email_data.get('processing_notes', f"Auto-verified on {current_time}")

            row_data = [
                date_value,                                    # Dátum (Date) - from due date as proper date
                payment_type,                                  # Fizetve (Corporate invoice or Personal)
                '',                                            # Bevétel HUF (Income HUF) - empty for expenses
                amount_value,                                  # Kiadás HUF (Expense HUF) - as integer
                '',                                            # Bevétel EUR (Income EUR) - empty
                eur_column_value,                              # Kiadás EUR (Expense EUR) - from EUR or USD amount
                sheet_description,                             # Megjegyzés (Notes) - from rules
                email_data.get('dropbox_link', ''),            # Link a számlára (Link to invoice)
                '',                                            # Column2 (empty) - existing structure preserved
                gmail_message_id,                              # NEW Column J: Gmail Message ID
                verification_status,                           # NEW Column K: Verification Status
                verification_date,                             # NEW Column L: Verification Date
                processing_notes                               # NEW Column M: Processing Notes
            ]
            
            # Append to the next available row with value_input_option to preserve formatting
            self.worksheet.append_row(row_data, value_input_option='USER_ENTERED')
            
            logger.info(f"Appended invoice data to 2025 sheet: {email_data.get('pdf_filename', 'unknown')}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log to 2025 worksheet: {e}")
            return False
    
    async def update_processing_status(
        self, 
        gmail_message_id: str, 
        status: str, 
        dropbox_link: str = None,
        error_message: str = None
    ) -> bool:
        """Update processing status for a specific email."""
        if not self.worksheet:
            logger.error("Sheets client not initialized")
            return False
        
        try:
            # Find the row with the Gmail message ID
            gmail_id_col = self.headers.index('Gmail Message ID') + 1  # 1-based indexing
            
            try:
                cell = self.worksheet.find(gmail_message_id)
                row_num = cell.row
            except Exception as cell_not_found_error:
                # Handle cell not found (gspread exception naming can vary)
                if "not found" in str(cell_not_found_error).lower():
                    logger.warning(f"Gmail message ID not found in spreadsheet: {gmail_message_id}")
                    return False
                else:
                    # Re-raise if it's not a "not found" error
                    raise cell_not_found_error
            
            # Update columns
            updates = []
            
            # Processing Status
            status_col = self.headers.index('Processing Status') + 1
            updates.append({
                'range': f"{gspread.utils.rowcol_to_a1(row_num, status_col)}",
                'values': [[status]]
            })
            
            # Dropbox Link
            if dropbox_link:
                dropbox_col = self.headers.index('Dropbox Link') + 1
                updates.append({
                    'range': f"{gspread.utils.rowcol_to_a1(row_num, dropbox_col)}",
                    'values': [[dropbox_link]]
                })
            
            # Error Message
            if error_message:
                error_col = self.headers.index('Error Message') + 1
                updates.append({
                    'range': f"{gspread.utils.rowcol_to_a1(row_num, error_col)}",
                    'values': [[error_message]]
                })
            
            # Update timestamp
            timestamp_col = self.headers.index('Timestamp') + 1
            updates.append({
                'range': f"{gspread.utils.rowcol_to_a1(row_num, timestamp_col)}",
                'values': [[datetime.now().isoformat()]]
            })
            
            # Batch update
            if updates:
                self.worksheet.batch_update(updates)
                logger.info(f"Updated processing status to {status} for message ID: {gmail_message_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update processing status: {e}")
            return False
    
    async def get_processing_stats(self) -> Dict[str, Any]:
        """Get processing statistics from the spreadsheet."""
        if not self.worksheet:
            return {"error": "Sheets client not initialized"}
        
        try:
            # Get all data
            all_values = self.worksheet.get_all_values()
            
            if len(all_values) <= 1:  # Only headers or empty
                return {
                    "total_processed": 0,
                    "completed": 0,
                    "failed": 0,
                    "pending": 0
                }
            
            # Count statuses
            status_col = self.headers.index('Processing Status')
            statuses = [row[status_col] for row in all_values[1:] if len(row) > status_col]
            
            stats = {
                "total_processed": len(statuses),
                "completed": statuses.count('COMPLETED'),
                "failed": statuses.count('FAILED'),
                "pending": statuses.count('PENDING'),
                "processing": statuses.count('PROCESSING')
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get processing stats: {e}")
            return {"error": str(e)}


async def create_sheets_client() -> Optional[SheetsClient]:
    """Create and initialize Google Sheets client."""
    try:
        client = SheetsClient()
        initialized = await client.initialize()
        
        if not initialized:
            logger.error("Failed to initialize Sheets client")
            return None
        
        logger.info("Sheets client created and initialized successfully")
        return client
        
    except Exception as e:
        logger.error(f"Failed to create Sheets client: {e}")
        return None