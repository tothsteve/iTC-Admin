#!/usr/bin/env python3
"""
Manual Invoice Processor - Command-line tool for processing individual PDF invoices

Usage:
    python scripts/manual_invoice_processor.py invoice.pdf
    python scripts/manual_invoice_processor.py --pdf invoice.pdf --partner Danubius
    python scripts/manual_invoice_processor.py invoice.pdf --dry-run
"""

import sys
import os
import argparse
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
import re

# Add src directory to Python path (same pattern as integrated_workflow.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

try:
    import PyPDF2
except ImportError:
    print("❌ Error: PyPDF2 not installed. Install with: pip install PyPDF2")
    sys.exit(1)

try:
    from pdf2image import convert_from_path
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("⚠️  Warning: OCR not available. Install with: pip install pdf2image pytesseract")

from config import Settings, get_settings
from invoice_processor import InvoiceRulesEngine, InvoiceClassification
from sheets.client import SheetsClient
from dropbox.local_sync import LocalDropboxManager
from utils.logger import setup_logging, get_logger


class ManualInvoiceProcessor:
    """Processes individual PDF invoices with user interaction."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.logger = get_logger(__name__)
        self.rules_engine = None
        self.sheets_client = None
        self.dropbox_manager = None

    async def initialize(self) -> bool:
        """Initialize all clients and services."""
        try:
            print("\n🚀 Initializing invoice processor...\n")

            # Load rules engine
            self.rules_engine = InvoiceRulesEngine()
            if not self.rules_engine.rules:
                print("❌ Failed to load invoice rules")
                return False
            print(f"✅ Loaded {len(self.rules_engine.rules)} partner rules")

            # Initialize sheets client
            self.sheets_client = SheetsClient()
            if not await self.sheets_client.initialize():
                print("❌ Failed to initialize Google Sheets client")
                return False
            print("✅ Connected to Google Sheets")

            # Initialize dropbox manager
            self.dropbox_manager = LocalDropboxManager()
            if not await self.dropbox_manager.initialize():
                print("❌ Failed to initialize Dropbox manager")
                return False
            print("✅ Initialized Dropbox sync folder")

            print("\n✅ All systems ready!\n")
            return True

        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            print(f"\n❌ Initialization failed: {e}\n")
            return False

    def extract_pdf_text(self, pdf_path: Path) -> Optional[str]:
        """Extract text content from PDF file, with OCR fallback for scanned PDFs."""
        try:
            print(f"📝 Extracting text from PDF: {pdf_path.name}")

            # Try standard PyPDF2 extraction first
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""

                for page_num, page in enumerate(pdf_reader.pages, 1):
                    page_text = page.extract_text()
                    text += page_text + "\n"

                print(f"   PyPDF2 extracted {len(text)} characters from {len(pdf_reader.pages)} pages")

                # If very little text extracted, it's likely a scanned/image PDF
                if len(text.strip()) < 100:
                    print(f"⚠️  Very little text extracted - likely a scanned PDF")

                    if OCR_AVAILABLE:
                        print(f"🔍 Attempting OCR extraction...")
                        ocr_text = self._extract_pdf_with_ocr(pdf_path)

                        if ocr_text and len(ocr_text.strip()) > len(text.strip()):
                            print(f"✅ OCR extracted {len(ocr_text)} characters")
                            return ocr_text
                        else:
                            print(f"⚠️  OCR extraction didn't improve results")
                    else:
                        print(f"⚠️  OCR not available. Install with: pip install pdf2image pytesseract")
                        print(f"   Also ensure Tesseract is installed: brew install tesseract")

                if len(text.strip()) > 0:
                    print(f"✅ Using extracted text ({len(text)} characters)")
                    return text
                else:
                    print(f"❌ No text could be extracted from PDF")
                    return None

        except Exception as e:
            print(f"❌ Failed to extract PDF text: {e}")
            self.logger.error(f"PDF extraction failed: {e}")
            return None

    def _extract_pdf_with_ocr(self, pdf_path: Path) -> Optional[str]:
        """Extract text using OCR for scanned/image-based PDFs."""
        try:
            # Convert PDF pages to images
            images = convert_from_path(str(pdf_path), dpi=300)

            # Extract text from each image
            text = ""
            for i, image in enumerate(images, 1):
                print(f"   OCR processing page {i}/{len(images)}...")
                page_text = pytesseract.image_to_string(image, lang='hun+eng')
                text += page_text + "\n"

            return text

        except Exception as e:
            print(f"   OCR extraction error: {e}")
            self.logger.error(f"OCR extraction failed: {e}")
            return None

    def auto_detect_partner(self, pdf_path: Path, pdf_text: str) -> Optional[InvoiceClassification]:
        """Try to auto-detect partner from PDF content and filename."""
        try:
            print("\n🔍 Auto-detecting business partner...")

            # Create mock email_data with PDF filename and text
            email_data = {
                'sender': '',
                'subject': pdf_path.name,  # Use filename as subject
                'body': pdf_text[:1000],   # Use first 1000 chars of PDF text
                'attachments': [{'filename': pdf_path.name}]
            }

            # Try classification
            classification = self.rules_engine.classify_email(email_data)

            if classification and classification.confidence > 0.3:
                print(f"   Found potential match: {classification.partner_name}")
                print(f"   Confidence: {classification.confidence:.2f}")
                print(f"   Matched patterns: {', '.join(classification.matched_patterns)}")
                return classification
            else:
                print("   No confident match found")
                return None

        except Exception as e:
            print(f"⚠️  Auto-detection failed: {e}")
            self.logger.error(f"Auto-detection error: {e}")
            return None

    def interactive_partner_selection(self, auto_classification: Optional[InvoiceClassification] = None) -> Optional[Dict[str, Any]]:
        """Show interactive menu for partner selection."""
        try:
            # If auto-classification exists, ask to confirm
            if auto_classification and auto_classification.confidence >= 0.5:
                print(f"\n❓ Detected partner: {auto_classification.partner_name}")
                choice = input("   Is this correct? (Y/n/show-all): ").strip().lower()

                if choice in ['', 'y', 'yes']:
                    # Get the full rule data
                    rule = self.rules_engine.rules.get(auto_classification.partner_name)
                    return rule
                elif choice not in ['show-all', 's']:
                    # User said no, fall through to manual selection
                    pass

            # Show list of all partners + manual option
            print("\n📋 Available business partners:\n")
            partners = list(self.rules_engine.rules.keys())

            for i, partner_name in enumerate(partners, 1):
                print(f"   {i:2d}. {partner_name}")

            print(f"\n    0. 🆕 Manual Entry (New/Other partner)")
            print(f"\n   Total: {len(partners)} partners")

            # Get user selection
            while True:
                try:
                    choice = input("\n❓ Select partner number or 0 for manual (q to quit): ").strip()

                    if choice.lower() == 'q':
                        return None

                    choice_num = int(choice)

                    if choice_num == 0:
                        # Manual entry mode
                        print("\n🆕 Manual Entry Mode")
                        return self._create_manual_partner_rule()
                    elif 1 <= choice_num <= len(partners):
                        selected_partner = partners[choice_num - 1]
                        rule = self.rules_engine.rules[selected_partner]
                        print(f"\n✅ Selected: {selected_partner}\n")
                        return rule
                    else:
                        print(f"   Invalid choice. Please enter 0-{len(partners)}")

                except ValueError:
                    print("   Invalid input. Please enter a number")
                except KeyboardInterrupt:
                    print("\n\n❌ Cancelled by user")
                    return None

        except Exception as e:
            print(f"❌ Partner selection failed: {e}")
            self.logger.error(f"Partner selection error: {e}")
            return None

    def _create_manual_partner_rule(self) -> Optional[Dict[str, Any]]:
        """Create a minimal partner rule for manual entry."""
        try:
            print("\n" + "="*60)
            print("🆕 MANUAL PARTNER ENTRY")
            print("="*60)
            print("   For store receipts, downloaded PDFs, or one-off vendors\n")

            # Get partner name
            partner_name = input("   Partner/Store Name: ").strip()
            if not partner_name:
                print("   Partner name required")
                return None

            print(f"   You'll manually enter all invoice data")

            # Create minimal rule
            manual_rule = {
                "name": partner_name,
                "description": f"Manual Entry - {partner_name}",
                "email_patterns": [],
                "subject_patterns": [],
                "invoice_type": "kiadas_vallalati",  # Default, will be prompted
                "payment_type": "Vállalati számla",
                "filename_prefix": partner_name[:15],  # Limit length
                "sheet_description": partner_name,  # Clean name without "Manual -" prefix
                "amount_extraction": {
                    "method": "none"  # Will enter manually
                },
                "due_date_extraction": {
                    "pdf_patterns": []
                },
                "manual_entry": True  # Flag for manual entry
            }

            print(f"\n✅ Created manual entry for: {partner_name}\n")
            return manual_rule

        except KeyboardInterrupt:
            print("\n\n❌ Cancelled by user")
            return None
        except Exception as e:
            print(f"❌ Failed to create manual partner: {e}")
            self.logger.error(f"Manual partner creation error: {e}")
            return None

    def extract_all_data(self, pdf_path: Path, pdf_text: str, partner_rule: Dict[str, Any],
                        classification: InvoiceClassification) -> Dict[str, Any]:
        """Extract all required data fields using partner rule."""
        try:
            data = {
                'partner_name': partner_rule['name'],
                'filename': pdf_path.name,
                'pdf_path': str(pdf_path),
            }

            # Check if this is manual entry mode
            if partner_rule.get('manual_entry', False):
                print("\n✍️  Manual Entry Mode - You'll enter all data in next step")
                print("   (System will still try to help with OCR text)")

                # DEBUG: Show snippet of OCR text
                print(f"\n   [DEBUG] OCR text preview (first 500 chars):")
                print(f"   {pdf_text[:500]}")
                print()

                # Try basic extraction as hints but don't rely on them
                # Look for common amount patterns (prioritize totals)
                amount_patterns = [
                    (r'ÖSSZESEN:\s*(\d+(?:\s+\d+)*)\s*F', 'ÖSSZESEN'),  # Total with spaces
                    (r'Bankkártya:\s*(\d+(?:\s+\d+)*)\s*F', 'Bankkártya'),  # Card payment
                    (r'Total:\s*(\d+(?:\s+\d+)*)', 'Total'),  # English total
                    (r'Végösszeg:\s*(\d+(?:\s+\d+)*)', 'Végösszeg'),  # Final total
                ]
                amount_hint = None
                for pattern, label in amount_patterns:
                    match = re.search(pattern, pdf_text, re.IGNORECASE)
                    if match:
                        amount_str = match.group(1).replace(' ', '')
                        try:
                            amount_hint = int(amount_str)
                            print(f"   [DEBUG] Matched pattern '{label}': {match.group(0)} -> {amount_hint}")
                            break
                        except:
                            pass

                if not amount_hint:
                    print(f"   [DEBUG] No amount patterns matched, trying fallback...")
                    # Fallback: find largest number in text (likely the total)
                    all_amounts = re.findall(r'(\d+(?:\s+\d+)+)\s*F', pdf_text)
                    if all_amounts:
                        # Convert all to integers and take the largest
                        amounts_int = []
                        for amt_str in all_amounts:
                            try:
                                amounts_int.append((int(amt_str.replace(' ', '')), amt_str))
                            except:
                                pass
                        if amounts_int:
                            amount_hint = max(amounts_int, key=lambda x: x[0])[0]
                            print(f"   [DEBUG] Using largest amount found: {amount_hint}")

                # Look for invoice number patterns
                invoice_number_hint = None
                inv_patterns = [
                    r'SZÁMLASZÁM:\s*([A-Z0-9/-]+)',
                    r'Invoice\s*#?\s*:?\s*([A-Z0-9/-]+)',
                ]
                for pattern in inv_patterns:
                    match = re.search(pattern, pdf_text, re.IGNORECASE)
                    if match:
                        invoice_number_hint = match.group(1).strip()
                        break

                # Try to extract invoice date from PDF or filename
                invoice_date_hint = self._extract_invoice_date(pdf_path.name, pdf_text, partner_rule)
                if not invoice_date_hint:
                    invoice_date_hint = datetime.now().strftime('%Y%m%d')

                # For receipts, due date is usually same as invoice date (paid immediately)
                # Propose invoice date as due date
                due_date_hint = invoice_date_hint

                # Set data with hints (will be confirmed/edited by user)
                data['amount_huf'] = amount_hint
                data['amount_huf_display'] = f"{amount_hint:,} HUF" if amount_hint else ""
                data['amount_eur'] = None
                data['amount_eur_display'] = ""
                data['due_date'] = due_date_hint
                # Format due date for display
                try:
                    due_date_obj = datetime.strptime(due_date_hint, '%Y%m%d')
                    data['due_date_display'] = due_date_obj.strftime('%Y-%m-%d')
                except:
                    data['due_date_display'] = ""
                data['invoice_date'] = invoice_date_hint
                # Format invoice date for display
                try:
                    invoice_date_obj = datetime.strptime(invoice_date_hint, '%Y%m%d')
                    data['invoice_date_display'] = invoice_date_obj.strftime('%Y-%m-%d')
                except:
                    data['invoice_date_display'] = datetime.now().strftime('%Y-%m-%d')
                data['invoice_number'] = invoice_number_hint or pdf_path.stem
                data['invoice_number_display'] = invoice_number_hint or pdf_path.stem
                data['classification'] = classification

                if amount_hint:
                    print(f"   💡 Hint: Found amount {amount_hint:,} HUF in OCR text")
                if invoice_number_hint:
                    print(f"   💡 Hint: Found invoice number {invoice_number_hint} in OCR text")
                print(f"   💡 Hint: Proposing invoice date as due date (receipt paid immediately)")

                return data

            # Normal extraction mode (existing code)
            print("\n💰 Extracting invoice data...")

            # Create mock email_data for extraction
            email_data = {
                'sender': '',
                'subject': pdf_path.name,
                'body': '',
                'attachments': [{'filename': pdf_path.name}]
            }

            # Extract amount (HUF)
            amount = self.rules_engine.extract_amount(email_data, pdf_text, classification)
            data['amount_huf'] = amount
            data['amount_huf_display'] = f"{int(amount):,} HUF" if amount else "Not found"

            # Extract EUR amount (if applicable)
            eur_amount = self.rules_engine.extract_eur_amount(email_data, pdf_text, classification)
            data['amount_eur'] = eur_amount
            data['amount_eur_display'] = f"{eur_amount:.2f} EUR" if eur_amount else ""

            # Extract due date
            due_date = self.rules_engine.extract_due_date(pdf_text, classification)
            data['due_date'] = due_date
            if due_date and len(due_date) == 8:
                # Format as YYYY-MM-DD for display
                try:
                    date_obj = datetime.strptime(due_date, '%Y%m%d')
                    data['due_date_display'] = date_obj.strftime('%Y-%m-%d')
                except:
                    data['due_date_display'] = due_date
            else:
                data['due_date_display'] = "Not found"

            # Try to extract invoice date (from filename or PDF)
            invoice_date = self._extract_invoice_date(pdf_path.name, pdf_text, partner_rule)
            data['invoice_date'] = invoice_date
            if invoice_date and len(invoice_date) == 8:
                try:
                    date_obj = datetime.strptime(invoice_date, '%Y%m%d')
                    data['invoice_date_display'] = date_obj.strftime('%Y-%m-%d')
                except:
                    data['invoice_date_display'] = invoice_date
            else:
                # Use current date as fallback
                data['invoice_date'] = datetime.now().strftime('%Y%m%d')
                data['invoice_date_display'] = datetime.now().strftime('%Y-%m-%d')

            # Try to extract invoice number (from filename)
            invoice_number = self._extract_invoice_number(pdf_path.name, pdf_text, partner_rule)
            data['invoice_number'] = invoice_number
            data['invoice_number_display'] = invoice_number if invoice_number else "Not found"

            # Add classification info
            data['classification'] = classification

            return data

        except Exception as e:
            print(f"❌ Data extraction failed: {e}")
            self.logger.error(f"Data extraction error: {e}")
            return {}

    def _extract_invoice_date(self, filename: str, pdf_text: str, rule: Dict[str, Any]) -> Optional[str]:
        """Extract invoice date from filename or PDF."""
        # Try to find date in filename (YYYYMMDD format)
        date_match = re.search(r'(\d{8})', filename)
        if date_match:
            try:
                # Validate it's a real date
                datetime.strptime(date_match.group(1), '%Y%m%d')
                return date_match.group(1)
            except:
                pass

        # Try to find date in PDF with common patterns
        date_patterns = [
            r'Kiállítás.*?(\d{4})[.-](\d{1,2})[.-](\d{1,2})',
            r'Kelt.*?(\d{4})[.-](\d{1,2})[.-](\d{1,2})',
            r'Invoice\s+date.*?(\d{4})[.-](\d{1,2})[.-](\d{1,2})',
            r'Date.*?(\d{4})[.-](\d{1,2})[.-](\d{1,2})',
        ]

        for pattern in date_patterns:
            match = re.search(pattern, pdf_text, re.IGNORECASE)
            if match:
                try:
                    if len(match.groups()) == 3:
                        year, month, day = match.groups()
                        date_obj = datetime(int(year), int(month), int(day))
                        return date_obj.strftime('%Y%m%d')
                except:
                    continue

        return None

    def _extract_invoice_number(self, filename: str, pdf_text: str, rule: Dict[str, Any]) -> Optional[str]:
        """Extract invoice number from filename or PDF."""
        # Remove common extensions and prefixes from filename
        clean_name = filename.replace('.pdf', '').replace('.PDF', '')

        # Try to find invoice number patterns in filename
        # Common patterns: SCHNH-2025-3839, KI2501065, SZLA-01730, etc.
        invoice_patterns = [
            r'([A-Z]+-\d+-\d+)',      # SCHNH-2025-3839
            r'([A-Z]+\d+)',            # KI2501065
            r'([A-Z]+-\d+)',           # SZLA-01730
            r'(\d{7,})',               # 7+ digit numbers
        ]

        for pattern in invoice_patterns:
            match = re.search(pattern, clean_name)
            if match:
                return match.group(1)

        # If not found in filename, try PDF text
        pdf_patterns = [
            r'Számlaszám[:\s]+([A-Z0-9-]+)',
            r'Invoice\s+number[:\s]+([A-Z0-9-]+)',
            r'Invoice\s+#[:\s]*([A-Z0-9-]+)',
        ]

        for pattern in pdf_patterns:
            match = re.search(pattern, pdf_text, re.IGNORECASE)
            if match:
                return match.group(1)

        # Fallback: use filename without extension
        return clean_name

    def confirm_extracted_data(self, data: Dict[str, Any], pdf_text: str, partner_rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Display extracted data and prompt user to confirm/edit."""
        try:
            print("\n" + "="*60)
            print("📋 EXTRACTED DATA")
            print("="*60)
            print(f"   Partner Name:    {data.get('partner_name', 'N/A')}")
            print(f"   Amount (HUF):    {data.get('amount_huf_display', 'N/A')}")
            if data.get('amount_eur_display'):
                print(f"   Amount (EUR):    {data.get('amount_eur_display', 'N/A')}")
            print(f"   Due Date:        {data.get('due_date_display', 'N/A')}")
            print(f"   Invoice Date:    {data.get('invoice_date_display', 'N/A')}")
            print(f"   Invoice Number:  {data.get('invoice_number_display', 'N/A')}")
            print("="*60)

            print("\n❓ Confirm extracted data:\n")

            # Confirm partner name
            confirmed_data = {}
            confirmed_data['partner_name'] = self._prompt_for_field(
                "Partner Name",
                data.get('partner_name'),
                lambda x: len(x) > 0
            )

            # Confirm amount (HUF)
            if data.get('amount_huf'):
                amount_str = self._prompt_for_field(
                    "Amount (HUF)",
                    str(int(data['amount_huf'])),
                    lambda x: x.replace(',', '').replace(' ', '').isdigit()
                )
                confirmed_data['amount_huf'] = float(amount_str.replace(',', '').replace(' ', ''))
            else:
                amount_str = self._prompt_for_field(
                    "Amount (HUF)",
                    "",
                    lambda x: x == "" or x.replace(',', '').replace(' ', '').isdigit(),
                    allow_empty=True
                )
                confirmed_data['amount_huf'] = float(amount_str.replace(',', '').replace(' ', '')) if amount_str else None

            # Confirm EUR amount (if applicable)
            if data.get('amount_eur') or data.get('amount_eur_display'):
                eur_str = self._prompt_for_field(
                    "Amount (EUR)",
                    str(data['amount_eur']) if data.get('amount_eur') else "",
                    lambda x: x == "" or self._is_valid_decimal(x),
                    allow_empty=True
                )
                confirmed_data['amount_eur'] = float(eur_str) if eur_str else None
            else:
                confirmed_data['amount_eur'] = None

            # Confirm due date
            due_date_str = self._prompt_for_field(
                "Due Date (YYYY-MM-DD)",
                data.get('due_date_display', ''),
                lambda x: x == "" or self._is_valid_date(x),
                allow_empty=True
            )
            if due_date_str:
                try:
                    date_obj = datetime.strptime(due_date_str, '%Y-%m-%d')
                    confirmed_data['due_date'] = date_obj.strftime('%Y%m%d')
                except:
                    confirmed_data['due_date'] = None
            else:
                confirmed_data['due_date'] = None

            # Confirm invoice date
            invoice_date_str = self._prompt_for_field(
                "Invoice Date (YYYY-MM-DD)",
                data.get('invoice_date_display', ''),
                lambda x: self._is_valid_date(x)
            )
            try:
                date_obj = datetime.strptime(invoice_date_str, '%Y-%m-%d')
                confirmed_data['invoice_date'] = date_obj.strftime('%Y%m%d')
            except:
                confirmed_data['invoice_date'] = datetime.now().strftime('%Y%m%d')

            # Confirm invoice number (optional)
            invoice_num_str = self._prompt_for_field(
                "Invoice Number (optional, press Enter to skip)",
                data.get('invoice_number_display', ''),
                lambda x: True,  # Always valid, can be empty
                allow_empty=True
            )
            confirmed_data['invoice_number'] = invoice_num_str if invoice_num_str else f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}"

            # Detect business card and prompt for invoice type
            has_business_card = self._detect_business_card(pdf_text)
            invoice_type, payment_type = self._prompt_invoice_type(partner_rule, has_business_card)

            confirmed_data['invoice_type'] = invoice_type
            confirmed_data['payment_type'] = payment_type

            # Copy other data
            confirmed_data['filename'] = data.get('filename')
            confirmed_data['pdf_path'] = data.get('pdf_path')
            confirmed_data['classification'] = data.get('classification')
            confirmed_data['partner_rule'] = partner_rule

            return confirmed_data

        except KeyboardInterrupt:
            print("\n\n❌ Cancelled by user")
            return None
        except Exception as e:
            print(f"\n❌ Data confirmation failed: {e}")
            self.logger.error(f"Data confirmation error: {e}")
            return None

    def _prompt_for_field(self, field_name: str, current_value: str,
                         validation_func, allow_empty: bool = False) -> str:
        """Prompt user for a single field with validation."""
        while True:
            prompt = f"   {field_name} ({current_value}): "
            value = input(prompt).strip()

            # If empty, use current value
            if not value:
                if current_value or allow_empty:
                    return current_value
                else:
                    print(f"      ⚠️  {field_name} cannot be empty")
                    continue

            # Validate
            if validation_func(value):
                return value
            else:
                print(f"      ⚠️  Invalid {field_name} format")

    def _is_valid_date(self, date_str: str) -> bool:
        """Validate date string in YYYY-MM-DD format."""
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except:
            return False

    def _is_valid_decimal(self, value_str: str) -> bool:
        """Validate decimal number string."""
        try:
            float(value_str)
            return True
        except:
            return False

    def _detect_business_card(self, pdf_text: str) -> bool:
        """
        Detect if business card (ending in 5059) is present in PDF.

        Looks for card number patterns like:
        - 1234 5678 9012 5059
        - 1234-5678-9012-5059
        - 1234567890125059
        - **** **** **** 5059

        Returns True if card ending in 5059 is found.
        """
        try:
            # Card number patterns (with various separators)
            card_patterns = [
                r'\b\d{4}[\s\-]*\d{4}[\s\-]*\d{4}[\s\-]*5059\b',  # Full card with 5059 ending
                r'\*{4}[\s\-]*\*{4}[\s\-]*\*{4}[\s\-]*5059\b',    # Masked card with 5059 ending
                r'\b\d{12}5059\b',                                 # 16 digits ending in 5059
            ]

            for pattern in card_patterns:
                match = re.search(pattern, pdf_text)
                if match:
                    print(f"   💳 Detected business card ending in 5059")
                    return True

            return False

        except Exception as e:
            self.logger.error(f"Card detection error: {e}")
            return False

    def _prompt_invoice_type(self, partner_rule: Dict[str, Any], has_business_card: bool) -> tuple[str, str]:
        """
        Prompt user to select invoice type (directory).

        Args:
            partner_rule: Partner rule configuration
            has_business_card: True if business card (5059) detected in PDF

        Returns:
            Tuple of (invoice_type, payment_type)
        """
        try:
            # Determine smart default
            if has_business_card:
                # Business card detected → default to business expense
                default_type = "kiadas_vallalati"
                default_payment = "Vállalati számla"
                default_choice = "1"
                reason = "business card ending in 5059 detected"
            else:
                # Use partner rule default or fallback to business
                default_type = partner_rule.get('invoice_type', 'kiadas_vallalati')
                default_payment = partner_rule.get('payment_type', 'Vállalati számla')
                default_choice = "1" if default_type == "kiadas_vallalati" else "2"
                reason = f"partner default: {default_type}"

            print(f"\n💼 Invoice Type Selection:")
            print(f"   Smart default: {'Bejövő (Business)' if default_choice == '1' else 'Pénztár (Personal)'}")
            print(f"   Reason: {reason}")
            print("\n   Choose directory:")
            print("   1. Bejövő - Business expense (Vállalati számla)")
            print("   2. Pénztár - Personal expense (Saját)")

            while True:
                choice = input(f"\n   Select 1 or 2 (default: {default_choice}): ").strip()

                # Use default if empty
                if not choice:
                    choice = default_choice

                if choice == "1":
                    return "kiadas_vallalati", "Vállalati számla"
                elif choice == "2":
                    return "kiadas_penztár", "Saját"
                else:
                    print("      ⚠️  Invalid choice. Please enter 1 or 2")

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.error(f"Invoice type prompt error: {e}")
            # Fallback to safe default
            return "kiadas_vallalati", "Vállalati számla"

    def _get_simple_folder_path(self, invoice_type: str) -> str:
        """
        Generate simple folder name (relative path) without partner subfolders.

        Returns just the folder name:
        - "Bejövő" for business expenses
        - "Pénztár" for personal expenses

        The dropbox manager will combine this with: base/year/folder_name/

        Args:
            invoice_type: Either 'kiadas_vallalati' or 'kiadas_penztár'

        Returns:
            Folder name as string (e.g., "Bejövő" or "Pénztár")
        """
        # Map invoice type to folder name (just the folder name, not full path)
        folder_name = "Bejövő" if invoice_type == "kiadas_vallalati" else "Pénztár"

        return folder_name

    async def check_duplicate(self, invoice_number: str, filename: str) -> Optional[Dict[str, Any]]:
        """Check if invoice already exists in Google Sheets."""
        try:
            print("\n🔍 Checking for duplicates...")

            if not self.sheets_client or not self.sheets_client.worksheet:
                print("⚠️  Cannot check duplicates (Sheets not initialized)")
                return None

            # Search for invoice number or filename in the sheet
            try:
                # Try to find invoice number in the sheet
                if invoice_number:
                    cell = self.sheets_client.worksheet.find(invoice_number)
                    if cell:
                        row_data = self.sheets_client.worksheet.row_values(cell.row)
                        print(f"⚠️  Found duplicate invoice number at row {cell.row}")
                        return {
                            'found': True,
                            'row': cell.row,
                            'data': row_data
                        }

                # Try to find filename
                if filename:
                    cell = self.sheets_client.worksheet.find(filename)
                    if cell:
                        row_data = self.sheets_client.worksheet.row_values(cell.row)
                        print(f"⚠️  Found duplicate filename at row {cell.row}")
                        return {
                            'found': True,
                            'row': cell.row,
                            'data': row_data
                        }

                print("✅ No duplicate found")
                return None

            except Exception as e:
                # No match found (gspread raises exception when not found)
                print("✅ No duplicate found")
                return None

        except Exception as e:
            print(f"⚠️  Duplicate check failed: {e}")
            self.logger.error(f"Duplicate check error: {e}")
            return None

    def handle_duplicate(self, duplicate_info: Dict[str, Any]) -> bool:
        """Show duplicate warning and ask user to proceed."""
        try:
            print("\n" + "="*60)
            print("⚠️  DUPLICATE INVOICE DETECTED")
            print("="*60)
            print(f"   Row: {duplicate_info['row']}")

            # Show first few columns of duplicate
            row_data = duplicate_info['data']
            if len(row_data) >= 7:
                print(f"   Date: {row_data[0] if len(row_data) > 0 else ''}")
                print(f"   Amount: {row_data[3] if len(row_data) > 3 else ''} HUF")
                print(f"   Description: {row_data[6] if len(row_data) > 6 else ''}")

            print("="*60)

            choice = input("\n❓ Proceed anyway? (y/N): ").strip().lower()
            return choice in ['y', 'yes']

        except Exception as e:
            print(f"❌ Error handling duplicate: {e}")
            return False

    def generate_filename(self, data: Dict[str, Any], original_filename: str) -> str:
        """Generate standardized filename: YYYYMMDD_Prefix.pdf (simple, no invoice number)"""
        try:
            # Get date prefix from invoice date
            invoice_date = data.get('invoice_date', datetime.now().strftime('%Y%m%d'))

            # Get partner prefix from rule (check partner_rule first for manual entries)
            partner_rule = data.get('partner_rule')
            if not partner_rule:
                # Try to get from rules engine
                partner_rule = self.rules_engine.rules.get(data['partner_name'])

            if partner_rule:
                prefix = partner_rule.get('filename_prefix', data['partner_name'][:15])
            else:
                prefix = data['partner_name'][:15]

            # Simple filename: just date and partner
            # Add a counter if needed to avoid duplicates
            new_filename = f"{invoice_date}_{prefix}.pdf"

            return new_filename

        except Exception as e:
            print(f"⚠️  Filename generation failed, using original: {e}")
            return original_filename

    async def copy_to_dropbox(self, pdf_path: Path, new_filename: str,
                             data: Dict[str, Any]) -> Optional[str]:
        """Copy file to Dropbox with proper folder structure."""
        try:
            print(f"\n📁 New filename: {new_filename}")

            # Create temporary file with new name
            temp_path = pdf_path.parent / new_filename
            if temp_path != pdf_path:
                import shutil
                shutil.copy2(pdf_path, temp_path)
            else:
                temp_path = pdf_path

            # Create mock email_data for copy_pdf
            email_data = {
                'sender': '',
                'subject': new_filename,
                'attachments': [{'filename': new_filename}]
            }

            classification = data.get('classification')

            print(f"📂 Copying to Dropbox...")
            dropbox_path = await self.dropbox_manager.copy_pdf(
                temp_path,
                email_data,
                classification
            )

            # Clean up temp file if created
            if temp_path != pdf_path:
                temp_path.unlink()

            if dropbox_path:
                print(f"✅ File copied to: {dropbox_path}")
                return dropbox_path
            else:
                print("❌ Failed to copy file to Dropbox")
                return None

        except Exception as e:
            print(f"❌ Dropbox copy failed: {e}")
            self.logger.error(f"Dropbox copy error: {e}")
            return None

    async def log_to_sheets(self, data: Dict[str, Any], dropbox_path: str) -> bool:
        """Log invoice to Google Sheets."""
        try:
            print(f"\n📊 Logging to Google Sheets...")

            # Get partner rule (check partner_rule first for manual entries)
            partner_rule = data.get('partner_rule')
            if not partner_rule:
                # Try to get from rules engine
                partner_rule = self.rules_engine.rules.get(data['partner_name'])

            classification = data.get('classification')

            # Get sheet description and payment type safely
            if partner_rule:
                sheet_description = partner_rule.get('sheet_description', data['partner_name'])
                payment_type = partner_rule.get('payment_type', 'Vállalati számla')
            else:
                sheet_description = data['partner_name']
                payment_type = 'Vállalati számla'

            email_data = {
                'sender': '',
                'subject': data['filename'],
                'attachments': [{'filename': data['filename']}],
                'extracted_amount': data.get('amount_huf'),
                'extracted_eur_amount': data.get('amount_eur'),
                'due_date': data.get('due_date'),
                'classification': classification,
                'dropbox_link': dropbox_path,
                'gmail_message_id': f"manual_{data['invoice_number']}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                'verification_status': 'Verified (Manual)',
                'processing_notes': 'Manually processed via manual_invoice_processor.py',
                'sheet_description': sheet_description,
                'payment_type': payment_type
            }

            success = await self.sheets_client.log_email_processing(email_data)

            if success:
                print("✅ Successfully logged to Google Sheets")
                return True
            else:
                print("❌ Failed to log to Google Sheets")
                return False

        except Exception as e:
            print(f"❌ Sheets logging failed: {e}")
            self.logger.error(f"Sheets logging error: {e}")
            return False

    async def process_pdf(self, pdf_path: str, partner_name: Optional[str] = None,
                         dry_run: bool = False) -> bool:
        """Main processing workflow."""
        try:
            # 1. Validate PDF file exists (resolve absolute path first)
            pdf_file = Path(pdf_path).resolve()
            if not pdf_file.exists():
                print(f"❌ File not found: {pdf_path}")
                print(f"   (Resolved to: {pdf_file})")
                return False

            if not pdf_file.suffix.lower() == '.pdf':
                print(f"❌ Not a PDF file: {pdf_path}")
                return False

            print(f"\n📄 Processing PDF: {pdf_file.name}")
            print(f"   Size: {pdf_file.stat().st_size / 1024:.1f} KB")

            # 2. Extract PDF text
            pdf_text = self.extract_pdf_text(pdf_file)
            if not pdf_text:
                print("❌ Failed to extract PDF text")
                return False

            # 3. Partner identification
            partner_rule = None
            classification = None

            if partner_name:
                # Use specified partner
                partner_rule = self.rules_engine.rules.get(partner_name)
                if not partner_rule:
                    print(f"❌ Partner not found: {partner_name}")
                    return False
                print(f"\n✅ Using specified partner: {partner_name}")

                # Create classification
                email_data = {
                    'sender': '',
                    'subject': pdf_file.name,
                    'body': pdf_text[:1000],
                    'attachments': [{'filename': pdf_file.name}]
                }
                classification = self.rules_engine.classify_email(email_data)
                if not classification:
                    # Create manual classification
                    classification = InvoiceClassification(
                        partner_name=partner_name,
                        invoice_type=partner_rule.get('invoice_type', 'kiadas_vallalati'),
                        payment_type=partner_rule.get('payment_type', 'Vállalati számla'),
                        folder_path=self.rules_engine._get_folder_path(
                            partner_rule.get('invoice_type', 'kiadas_vallalati')
                        ),
                        confidence=1.0
                    )
            else:
                # Auto-detect
                classification = self.auto_detect_partner(pdf_file, pdf_text)
                partner_rule = self.interactive_partner_selection(classification)

                if not partner_rule:
                    print("\n❌ No partner selected, aborting")
                    return False

                # Update classification with selected partner
                if not classification or classification.partner_name != partner_rule['name']:
                    classification = InvoiceClassification(
                        partner_name=partner_rule['name'],
                        invoice_type=partner_rule.get('invoice_type', 'kiadas_vallalati'),
                        payment_type=partner_rule.get('payment_type', 'Vállalati számla'),
                        folder_path=self.rules_engine._get_folder_path(
                            partner_rule.get('invoice_type', 'kiadas_vallalati')
                        ),
                        confidence=1.0
                    )

            # 4. Extract all data
            data = self.extract_all_data(pdf_file, pdf_text, partner_rule, classification)
            if not data:
                print("❌ Data extraction failed")
                return False

            # 5. Confirm data with user (includes invoice type selection)
            confirmed_data = self.confirm_extracted_data(data, pdf_text, partner_rule)
            if not confirmed_data:
                print("\n❌ Processing cancelled")
                return False

            # 6. Update classification with confirmed invoice type
            classification.invoice_type = confirmed_data['invoice_type']
            classification.payment_type = confirmed_data['payment_type']
            classification.folder_path = self._get_simple_folder_path(confirmed_data['invoice_type'])
            confirmed_data['classification'] = classification

            # Display full path for user clarity
            from datetime import datetime
            year = datetime.now().year
            full_folder_path = self.dropbox_manager.dropbox_folder / str(year) / classification.folder_path

            print(f"\n✅ Invoice will be saved to: {full_folder_path}")
            print(f"   Payment type: {classification.payment_type}")

            # 7. Check duplicates
            duplicate_info = await self.check_duplicate(
                confirmed_data.get('invoice_number'),
                confirmed_data.get('filename')
            )

            if duplicate_info and duplicate_info.get('found'):
                if not self.handle_duplicate(duplicate_info):
                    print("\n❌ Processing cancelled due to duplicate")
                    return False

            if dry_run:
                print("\n✅ DRY RUN - Skipping file copy and logging")
                print("\n" + "="*60)
                print("📊 SUMMARY (DRY RUN)")
                print("="*60)
                print(f"   Partner:      {confirmed_data['partner_name']}")
                print(f"   Amount:       {confirmed_data.get('amount_huf', 0):,.0f} HUF")
                if confirmed_data.get('amount_eur'):
                    print(f"   Amount (EUR): {confirmed_data['amount_eur']:.2f} EUR")
                print(f"   Due Date:     {confirmed_data.get('due_date', 'N/A')}")
                print(f"   Invoice #:    {confirmed_data.get('invoice_number', 'N/A')}")
                print("="*60)
                return True

            # 8. Generate new filename
            new_filename = self.generate_filename(confirmed_data, pdf_file.name)

            # 9. Copy to Dropbox
            dropbox_path = await self.copy_to_dropbox(pdf_file, new_filename, confirmed_data)
            if not dropbox_path:
                print("❌ Failed to copy to Dropbox")
                return False

            # 10. Log to Google Sheets
            success = await self.log_to_sheets(confirmed_data, dropbox_path)
            if not success:
                print("⚠️  File copied but logging to Sheets failed")
                return False

            # 11. Display success summary
            print("\n" + "="*60)
            print("✅ PROCESSING COMPLETE")
            print("="*60)
            print(f"   Partner:      {confirmed_data['partner_name']}")
            print(f"   Amount:       {confirmed_data.get('amount_huf', 0):,.0f} HUF")
            if confirmed_data.get('amount_eur'):
                print(f"   Amount (EUR): {confirmed_data['amount_eur']:.2f} EUR")
            print(f"   Due Date:     {confirmed_data.get('due_date', 'N/A')}")
            print(f"   Invoice #:    {confirmed_data.get('invoice_number', 'N/A')}")
            print(f"   Dropbox:      {dropbox_path}")
            print("="*60)
            print()

            return True

        except KeyboardInterrupt:
            print("\n\n❌ Processing cancelled by user")
            return False
        except Exception as e:
            print(f"\n❌ Processing failed: {e}")
            self.logger.error(f"Processing error: {e}", exc_info=True)
            return False


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Process a PDF invoice manually",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s invoice.pdf
  %(prog)s --pdf invoice.pdf --partner "Danubius Expert"
  %(prog)s invoice.pdf --dry-run
        """
    )

    parser.add_argument(
        'pdf_path',
        nargs='?',
        help='Path to PDF invoice file'
    )

    parser.add_argument(
        '--pdf',
        dest='pdf_path_alt',
        help='Path to PDF invoice file (alternative flag)'
    )

    parser.add_argument(
        '--partner',
        help='Partner name (skip auto-detection)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Test extraction without copying/logging'
    )

    args = parser.parse_args()

    # Get PDF path from either positional arg or --pdf flag
    pdf_path = args.pdf_path or args.pdf_path_alt

    if not pdf_path:
        parser.print_help()
        print("\n❌ Error: PDF path required")
        sys.exit(1)

    # Setup logging
    try:
        from dotenv import load_dotenv
        load_dotenv()

        settings = get_settings()
        setup_logging(settings)

        # Create processor
        processor = ManualInvoiceProcessor(settings)

        # Initialize
        if not await processor.initialize():
            sys.exit(1)

        # Process PDF
        success = await processor.process_pdf(
            pdf_path,
            partner_name=args.partner,
            dry_run=args.dry_run
        )

        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
