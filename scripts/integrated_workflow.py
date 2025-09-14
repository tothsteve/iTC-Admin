#!/usr/bin/env python3
"""Integrated workflow: Gmail → Download → Dropbox → Google Sheets."""

print("🚀 DEBUG: Script starting...")

import sys
import os
import asyncio
from pathlib import Path
from datetime import datetime
import uuid

# PDF processing
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import get_settings
from gmail.client import create_gmail_client
from sheets.client import create_sheets_client
from dropbox.local_sync import create_local_dropbox_manager
from invoice_processor import create_rules_engine


class IntegratedWorkflow:
    """Integrated workflow for processing emails with Google Sheets and Dropbox."""
    
    def __init__(self):
        self.settings = get_settings()
        self.gmail_client = None
        self.sheets_client = None
        self.dropbox_client = None
        self.rules_engine = None
        self.processed_emails = set()
        
    async def initialize(self) -> bool:
        """Initialize all clients."""
        print("🚀 Initializing Integrated Workflow")
        print("=" * 50)
        
        # Initialize Gmail client
        print("1. Initializing Gmail client...")
        self.gmail_client = await create_gmail_client()
        if not self.gmail_client:
            print("❌ Failed to initialize Gmail client")
            return False
        print("✅ Gmail client ready")
        
        # Initialize Google Sheets client
        print("\\n2. Initializing Google Sheets client...")
        self.sheets_client = await create_sheets_client()
        if not self.sheets_client:
            print("❌ Failed to initialize Google Sheets client")
            return False
        print("✅ Google Sheets client ready")
        
        # Initialize local Dropbox manager
        print("\\n3. Initializing local Dropbox manager...")
        self.dropbox_client = await create_local_dropbox_manager()
        if not self.dropbox_client:
            print("❌ Failed to initialize local Dropbox manager")
            return False
        print("✅ Local Dropbox manager ready")
        
        # Initialize rules engine
        print("\\n4. Initializing invoice rules engine...")
        self.rules_engine = create_rules_engine()
        if not self.rules_engine:
            print("❌ Failed to initialize rules engine")
            return False
        print("✅ Rules engine ready")
        print(f"   Loaded {len(self.rules_engine.rules)} partner rules")
        
        print("\\n🎉 All clients initialized successfully!")
        return True
    
    async def process_emails_once(self, hours_back: int = 24) -> int:
        """Process emails once and return number of processed emails."""
        print(f"\\n📧 Processing emails from last {hours_back} hours...")
        
        try:
            print(f"📧 DEBUG: About to call Gmail API for last {hours_back} hours...")
            # Get ALL emails with PDF attachments (no domain/subject filtering)
            # We'll use the rules engine to classify them instead
            emails = await self.gmail_client.get_all_recent_emails_with_pdfs(
                hours_back=hours_back,
                max_results=50
            )
            print(f"📧 DEBUG: Gmail API returned {len(emails)} emails")
            
            print(f"Found {len(emails)} emails with PDF attachments")
            
            # Filter new emails
            new_emails = []
            for email in emails:
                if email['id'] not in self.processed_emails:
                    new_emails.append(email)
                    self.processed_emails.add(email['id'])
            
            if not new_emails:
                print("ℹ️  No new emails to process")
                return 0
            
            print(f"🆕 Processing {len(new_emails)} new emails")
            
            processed_count = 0
            for email in new_emails:
                success = await self.process_single_email(email)
                if success:
                    processed_count += 1
            
            print(f"\\n✅ Successfully processed {processed_count}/{len(new_emails)} emails")
            return processed_count
            
        except Exception as e:
            print(f"❌ Error processing emails: {e}")
            return 0
    
    async def process_single_email(self, email: dict) -> bool:
        """Process a single email through the complete workflow."""
        print(f"\\n📧 Processing: {email['subject'][:50]}...")
        print(f"   From: {email['sender']}")
        print(f"   PDFs: {len(email.get('pdf_attachments', []))}")
        
        try:
            # Check if email should be excluded
            is_excluded, exclusion_reason = self.rules_engine.is_excluded(email)
            if is_excluded:
                print(f"   🚫 EXCLUDED: {exclusion_reason}")
                print(f"   ⏭️  Skipping processing")
                return True  # Return True to indicate successful handling (by exclusion)
            
            # Classify email using rules engine
            classification = self.rules_engine.classify_email(email)
            
            # Skip if no matching rule found
            if classification is None:
                print(f"   ⏭️  Skipping - no matching rule found")
                return True  # Return True to indicate successful handling (by skipping)
            
            print(f"   🏷️  Partner: {classification.partner_name} (confidence: {classification.confidence:.2f})")
            print(f"   🎯 Matched: {', '.join(classification.matched_patterns)}")
            print(f"   💰 Invoice Type: {classification.invoice_type}")
            print(f"   📁 Folder: {classification.folder_path}")
            
            # Create downloads directory
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            # Create folder structure based on classification
            year = datetime.now().year
            partner_folder = downloads_dir / str(year) / classification.folder_path
            partner_folder.mkdir(parents=True, exist_ok=True)
            
            # Create specific folder for this email
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            email_folder = partner_folder / f"{timestamp}_{email['id'][:8]}"
            email_folder.mkdir(exist_ok=True)
            
            # Process each PDF attachment (with filename filtering if specified)
            for attachment in email.get('pdf_attachments', []):
                # Check if this PDF should be processed based on filename patterns
                if self._should_process_pdf(attachment['filename'], classification):
                    success = await self.process_single_attachment(email, attachment, email_folder, classification)
                    if not success:
                        print(f"   ⚠️  Failed to process attachment: {attachment['filename']}")
                else:
                    print(f"   ⏭️  Skipped attachment (filename filter): {attachment['filename']}")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Error processing email: {e}")
            return False
    
    async def process_single_attachment(self, email: dict, attachment: dict, email_folder: Path, classification) -> bool:
        """Process a single PDF attachment through the complete workflow."""
        filename = attachment['filename']
        correlation_id = str(uuid.uuid4())[:8]
        
        print(f"   📎 Processing attachment: {filename}")
        
        try:
            # Step 1: Download PDF from Gmail
            print(f"      1. Downloading from Gmail...")
            attachment_data = await self.gmail_client.download_attachment(
                email['id'],
                attachment['attachment_id'],
                filename
            )
            
            if not attachment_data:
                await self._log_processing_error(email, attachment, "Failed to download from Gmail")
                return False
            
            # Step 2: Save locally with temporary name first
            temp_file_path = email_folder / filename
            with open(temp_file_path, 'wb') as f:
                f.write(attachment_data)
            
            size_mb = len(attachment_data) / (1024*1024)
            print(f"      ✅ Downloaded locally ({size_mb:.1f} MB)")
            
            # Step 3: Extract amount and due date from PDF if it's a high-confidence match
            extracted_amount = None
            extracted_eur_amount = None
            due_date = None
            if classification.confidence > 0.5 and PDF_AVAILABLE:
                print(f"      2. Extracting data from PDF...")
                try:
                    pdf_text = self._extract_pdf_text(temp_file_path)
                    if pdf_text:
                        # Extract amount
                        extracted_amount = self.rules_engine.extract_amount(email, pdf_text, classification)
                        if extracted_amount:
                            print(f"      💰 Extracted amount: {extracted_amount:,.0f} HUF")
                        else:
                            print(f"      ⚠️  No amount found in PDF")
                        
                        # Extract EUR amount if applicable
                        extracted_eur_amount = self.rules_engine.extract_eur_amount(email, pdf_text, classification)
                        if extracted_eur_amount:
                            print(f"      💶 Extracted EUR amount: {extracted_eur_amount:.2f} EUR")
                        
                        # Extract due date
                        due_date = self.rules_engine.extract_due_date(pdf_text, classification)
                        if due_date:
                            print(f"      📅 Extracted due date: {due_date}")
                        else:
                            # Use today's date if no due date found
                            due_date = datetime.now().strftime("%Y%m%d")
                            print(f"      📅 Using today as due date: {due_date}")
                    else:
                        print(f"      ⚠️  Could not extract text from PDF")
                        due_date = datetime.now().strftime("%Y%m%d")
                except Exception as e:
                    print(f"      ⚠️  PDF data extraction failed: {e}")
                    due_date = datetime.now().strftime("%Y%m%d")
            else:
                # For unknown invoices, use today's date
                due_date = datetime.now().strftime("%Y%m%d")
            
            # Step 4: Rename file with date prefix and rule prefix
            local_file_path = self._rename_file_with_prefixes(temp_file_path, classification, due_date)
            print(f"      📝 Renamed to: {local_file_path.name}")
            
            # Step 5: Copy to local Dropbox folder
            print(f"      3. Copying to Dropbox folder...")
            email_data = {
                'gmail_message_id': email['id'],
                'sender_email': email['sender'],
                'subject': email['subject']
            }
            
            dropbox_path = await self.dropbox_client.copy_pdf(local_file_path, email_data)
            if dropbox_path:
                print(f"      ✅ Copied to Dropbox: {dropbox_path}")
            else:
                print(f"      ⚠️  Dropbox copy failed")
            
            # Step 6: Log to Google Sheets with extracted data
            print(f"      4. Logging to Google Sheets...")
            
            # Skip Google Sheets logging for Szamfejtolap files
            if 'Szamfejtolap' in filename:
                print(f"      ⏭️  Skipping Google Sheets logging for Szamfejtolap file")
                sheets_success = True  # Consider it successful to continue workflow
            elif classification.partner_name == "Bérszámfejtés" and ('Adoesjarulekbefizetesek' in filename or 'Bankiutalasok' in filename):
                # Special handling for tax table files - extract table data
                sheets_success = await self._log_berszamfejtes_table_data(email, local_file_path, dropbox_path, classification, due_date, pdf_text)
                if sheets_success:
                    print(f"      ✅ Logged table data to Google Sheets")
                else:
                    print(f"      ⚠️  Table data logging failed")
            else:
                # Standard logging for other files
                # Get sheet description from rules
                sheet_description = None
                if classification.confidence > 0.5:
                    for rule_name, rule in self.rules_engine.rules.items():
                        if rule['name'] == classification.partner_name:
                            sheet_description = rule.get('sheet_description', '')
                            break
                
                sheets_data = {
                    'gmail_message_id': email['id'],
                    'sender_email': email['sender'],
                    'subject': email['subject'],
                    'pdf_filename': local_file_path.name,  # Use renamed filename
                    'pdf_size_bytes': len(attachment_data),
                    'local_path': str(local_file_path),
                    'dropbox_link': dropbox_path or '',
                    'processing_status': 'COMPLETED' if dropbox_path else 'PARTIAL',
                    'error_message': '' if dropbox_path else 'Dropbox copy failed',
                    'extracted_amount': extracted_amount,
                    'extracted_eur_amount': extracted_eur_amount,
                    'due_date': due_date,
                    'sheet_description': sheet_description,
                    'payment_type': classification.payment_type
                }
                
                sheets_success = await self.sheets_client.log_email_processing(sheets_data)
                if sheets_success:
                    print(f"      ✅ Logged to Google Sheets")
                else:
                    print(f"      ⚠️  Google Sheets logging failed")
            
            # Step 7: Save email metadata locally
            self._save_email_metadata(email_folder, email, attachment, dropbox_path, extracted_amount, due_date, local_file_path.name)
            
            print(f"      🎉 Completed processing: {filename}")
            return True
            
        except Exception as e:
            print(f"      ❌ Error processing {filename}: {e}")
            await self._log_processing_error(email, attachment, str(e))
            return False
    
    def _extract_pdf_text(self, pdf_path: Path) -> str:
        """Extract text from PDF file."""
        if not PDF_AVAILABLE:
            return ""
        
        try:
            with open(pdf_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
                
                extracted_text = text.strip()
                
                # Debug output removed - working perfectly
                
                return extracted_text
        except Exception as e:
            print(f"         Error extracting PDF text: {e}")
            return ""
    
    async def _log_berszamfejtes_table_data(self, email, file_path: Path, dropbox_path: str, classification, due_date: str, pdf_text: str) -> bool:
        """Extract table data from Bérszámfejtés tax files and log each row to Google Sheets"""
        try:
            filename = file_path.name
            
            # Determine which table to extract
            if 'Adoesjarulekbefizetesek' in filename:
                table_rows = self._extract_tax_table_data(pdf_text, 'Adoesjarulekbefizetesek')
            elif 'Bankiutalasok' in filename:
                table_rows = self._extract_tax_table_data(pdf_text, 'Bankiutalasok')
            else:
                return False
            
            if not table_rows:
                print(f"         ⚠️  No table data found in {filename}")
                return False
            
            print(f"         📊 Extracted {len(table_rows)} table rows from {filename}")
            
            # Log each table row as separate entry in Google Sheets
            success_count = 0
            for row in table_rows:
                sheets_data = {
                    'gmail_message_id': email['id'],
                    'sender_email': email['sender'],
                    'subject': email['subject'],
                    'pdf_filename': filename,
                    'pdf_size_bytes': 0,
                    'local_path': str(file_path),
                    'dropbox_link': dropbox_path or '',
                    'processing_status': 'COMPLETED',
                    'error_message': '',
                    'extracted_amount': row['amount'],
                    'extracted_eur_amount': None,
                    'due_date': due_date,
                    'sheet_description': f"{row['description']} - {row['account_number']} - {row['tax_code']}",
                    'payment_type': classification.payment_type
                }
                
                if await self.sheets_client.log_email_processing(sheets_data):
                    success_count += 1
            
            print(f"         ✅ Logged {success_count}/{len(table_rows)} table rows to Google Sheets")
            return success_count > 0
            
        except Exception as e:
            print(f"         ❌ Error extracting table data: {e}")
            return False
    
    def _extract_tax_table_data(self, pdf_text: str, table_type: str) -> list:
        """Extract table data from tax PDF text"""
        try:
            import re
            
            rows = []
            lines = pdf_text.split('\n')
            
            if table_type == 'Adoesjarulekbefizetesek':
                # Pattern: Description + Tax Code + Account Number + Amount
                # Example: NAV Szociális hozzájárulási adó beszedési számla 258 10032000-06055912 51 000
                pattern = r'(.*?)\s+(\d{3})\s+(\d{8}-\d{8})\s+([0-9\s]+)'
                
                for line in lines:
                    line = line.strip()
                    if not line or 'Összesen' in line or 'Adónem' in line:
                        continue
                    
                    match = re.search(pattern, line)
                    if match:
                        description = match.group(1).strip()
                        tax_code = match.group(2).strip()
                        account_number = match.group(3).strip()
                        amount_str = match.group(4).strip()
                        
                        # Clean up amount: "51 000" -> 51000
                        amount = int(amount_str.replace(' ', ''))
                        
                        # Skip header-like entries
                        if 'kód' in description.lower() or len(description) < 10:
                            continue
                        
                        rows.append({
                            'description': description,
                            'tax_code': tax_code,
                            'account_number': account_number,
                            'amount': amount
                        })
            
            elif table_type == 'Bankiutalasok':
                # Pattern: Name + Tax ID + Bank Account + Amount + Row Number
                # Example: Tóth István 8324193499 12100011-11409520-00000000 1,160,250 1.
                pattern = r'([A-ZÁÉÍÓÖŐÚÜŰa-záéíóöőúüű\s]+)\s+(\d{10})\s+([\d-]+)\s+([\d,]+)\s+\d+\.'
                
                for line in lines:
                    line = line.strip()
                    if not line or 'Mindösszesen' in line or 'Név' in line or 'Sorok' in line:
                        continue
                    
                    match = re.search(pattern, line)
                    if match:
                        name = match.group(1).strip()
                        tax_id = match.group(2).strip()
                        bank_account = match.group(3).strip()
                        amount_str = match.group(4).strip()
                        
                        # Clean up amount: "1,160,250" -> 1160250
                        amount = int(amount_str.replace(',', ''))
                        
                        # Map specific tax IDs/accounts to personalized names
                        if tax_id == "8324193499" or "12100011-11409520-00000000" in bank_account:
                            personalized_name = "Tóth István (Apa)"
                        elif tax_id == "8440961790" or "11600006-00000000-79306874" in bank_account:
                            personalized_name = "Tóth István"
                        else:
                            personalized_name = name
                        
                        rows.append({
                            'description': personalized_name,
                            'tax_code': tax_id,
                            'account_number': bank_account,
                            'amount': amount
                        })
            
            return rows
            
        except Exception as e:
            print(f"         Error parsing {table_type} table data: {e}")
            return []
    
    def _rename_file_with_prefixes(self, original_path: Path, classification, due_date: str) -> Path:
        """Rename file with date and rule prefix: YYYYMMDD_prefix_original_filename"""
        try:
            # Get filename prefix from rule
            rule_prefix = "UNK"  # Default for unknown
            if classification.confidence > 0.5:
                # Find the rule to get the prefix
                for rule_name, rule in self.rules_engine.rules.items():
                    if rule['name'] == classification.partner_name:
                        rule_prefix = rule.get('filename_prefix', 'UNK')
                        break
            
            # Build new filename: YYYYMMDD_prefix_original_filename
            original_name = original_path.name
            new_name = f"{due_date}_{rule_prefix}_{original_name}"
            new_path = original_path.parent / new_name
            
            # Rename the file
            original_path.rename(new_path)
            return new_path
            
        except Exception as e:
            print(f"         Warning: Could not rename file: {e}")
            return original_path
    
    def _should_process_pdf(self, filename: str, classification) -> bool:
        """Check if PDF should be processed based on filename patterns"""
        # If confidence is low (Unknown Invoice), process all PDFs
        if classification.confidence <= 0.5:
            return True
            
        # Get the rule for this classification
        rule = None
        for rule_name, rule_config in self.rules_engine.rules.items():
            if rule_config['name'] == classification.partner_name:
                rule = rule_config
                break
                
        if not rule:
            return True
            
        # Check if rule has PDF filename patterns
        pdf_filename_patterns = rule.get('pdf_filename_patterns', [])
        if not pdf_filename_patterns:
            # No filename patterns specified, process all PDFs
            return True
            
        # Check if filename matches any pattern
        filename_lower = filename.lower()
        for pattern in pdf_filename_patterns:
            if pattern.lower() in filename_lower:
                return True
                
        # No pattern matched
        return False
    
    def _save_email_metadata(self, email_folder: Path, email: dict, attachment: dict, dropbox_path: str, extracted_amount: float = None, due_date: str = None, renamed_filename: str = None):
        """Save email metadata to local file."""
        info_file = email_folder / "processing_info.txt"
        
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"Processing Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\\n")
            f.write(f"Gmail ID: {email['id']}\\n")
            f.write(f"Subject: {email['subject']}\\n")
            f.write(f"From: {email['sender']}\\n")
            f.write(f"Date: {email['date']}\\n")
            f.write(f"Thread ID: {email['thread_id']}\\n")
            f.write(f"\\nAttachment: {attachment['filename']}\\n")
            f.write(f"Size: {attachment.get('size', 0)} bytes\\n")
            f.write(f"Dropbox Path: {dropbox_path or 'Copy failed'}\\n")
            if extracted_amount:
                f.write(f"Extracted Amount: {extracted_amount:,.0f} HUF\\n")
            if due_date:
                f.write(f"Due Date: {due_date}\\n")
            if renamed_filename:
                f.write(f"Renamed File: {renamed_filename}\\n")
            f.write(f"\\nProcessing Status: {'COMPLETED' if dropbox_path else 'PARTIAL'}\\n")
    
    async def _log_processing_error(self, email: dict, attachment: dict, error_message: str):
        """Log processing error to Google Sheets."""
        if self.sheets_client:
            error_data = {
                'gmail_message_id': email['id'],
                'sender_email': email['sender'],
                'subject': email['subject'],
                'pdf_filename': attachment['filename'],
                'pdf_size_bytes': attachment.get('size', 0),
                'local_path': '',
                'dropbox_link': '',
                'processing_status': 'FAILED',
                'error_message': error_message
            }
            await self.sheets_client.log_email_processing(error_data)
    
    async def run_continuous(self, check_interval_minutes: int = 10):
        """Run continuous monitoring."""
        print(f"\\n🔄 Starting continuous monitoring (check every {check_interval_minutes} minutes)")
        print("Press Ctrl+C to stop")
        
        try:
            # Initial processing
            await self.process_emails_once(hours_back=24)
            
            while True:
                await asyncio.sleep(check_interval_minutes * 60)
                print(f"\\n⏰ {datetime.now().strftime('%H:%M:%S')} - Checking for new emails...")
                await self.process_emails_once(hours_back=1)
                
        except KeyboardInterrupt:
            print("\\n🛑 Continuous monitoring stopped by user")
        except Exception as e:
            print(f"\\n❌ Continuous monitoring error: {e}")
    
    async def get_processing_stats(self) -> dict:
        """Get processing statistics from all services."""
        stats = {
            "timestamp": datetime.now().isoformat(),
            "gmail": {},
            "sheets": {},
            "dropbox": {}
        }
        
        if self.gmail_client:
            stats["gmail"] = await self.gmail_client.get_processing_stats()
        
        if self.sheets_client:
            stats["sheets"] = await self.sheets_client.get_processing_stats()
        
        if self.dropbox_client:
            stats["dropbox"] = await self.dropbox_client.get_folder_stats()
        
        return stats


async def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Integrated Gmail + Dropbox + Sheets Workflow")
    parser.add_argument("--once", action="store_true", help="Run once instead of continuous")
    parser.add_argument("--hours", type=int, default=24, help="Hours back to check (default: 24)")
    parser.add_argument("--interval", type=int, default=10, help="Check interval in minutes (default: 10)")
    parser.add_argument("--stats", action="store_true", help="Show processing statistics")
    
    args = parser.parse_args()
    
    print("🔧 DEBUG: Arguments parsed successfully")
    print(f"🔧 DEBUG: hours={args.hours}, once={args.once}, stats={args.stats}")
    
    print("📧 Integrated Gmail → Dropbox → Sheets Workflow")
    print("=" * 60)
    
    print("🔧 DEBUG: Creating workflow object...")
    workflow = IntegratedWorkflow()
    
    print("🔧 DEBUG: About to initialize workflow...")
    # Initialize
    initialized = await workflow.initialize()
    print(f"🔧 DEBUG: Initialization result: {initialized}")
    if not initialized:
        print("❌ Failed to initialize workflow")
        return
    
    if args.stats:
        print("\\n📊 Processing Statistics:")
        stats = await workflow.get_processing_stats()
        
        for service, data in stats.items():
            if service != "timestamp":
                print(f"\\n{service.upper()}:")
                for key, value in data.items():
                    print(f"  {key}: {value}")
        return
    
    if args.once:
        print(f"\\n🔍 Single run mode - checking last {args.hours} hours")
        processed = await workflow.process_emails_once(hours_back=args.hours)
        print(f"\\n✅ Processed {processed} emails")
    else:
        await workflow.run_continuous(check_interval_minutes=args.interval)


if __name__ == "__main__":
    asyncio.run(main())