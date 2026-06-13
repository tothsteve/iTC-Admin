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
from utils.web_fetcher import create_web_fetcher


class IntegratedWorkflow:
    """Integrated workflow for processing emails with Google Sheets and Dropbox."""
    
    def __init__(self):
        self.settings = get_settings()
        self.gmail_client = None
        self.sheets_client = None
        self.dropbox_client = None
        self.rules_engine = None
        self.web_fetcher = None
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

        # Initialize web fetcher
        print("\\n5. Initializing web fetcher...")
        self.web_fetcher = create_web_fetcher()
        if not self.web_fetcher:
            print("❌ Failed to initialize web fetcher")
            return False
        print("✅ Web fetcher ready")

        print("\\n🎉 All clients initialized successfully!")
        return True
    
    async def process_emails_once(self, hours_back: int = 24) -> int:
        """Process emails once and return number of processed emails."""
        print(f"\\n📧 Processing emails from last {hours_back} hours...")
        
        try:
            print(f"📧 DEBUG: About to call Gmail API for last {hours_back} hours...")

            # Get ALL emails with PDF attachments (no domain/subject filtering)
            # We'll use the rules engine to classify them instead
            emails_with_pdfs = await self.gmail_client.get_all_recent_emails_with_pdfs(
                hours_back=hours_back,
                max_results=50
            )
            print(f"📧 DEBUG: Found {len(emails_with_pdfs)} emails with PDF attachments")

            # ALSO search for Yettel emails (which don't have PDF attachments)
            print(f"📧 DEBUG: Searching for Yettel emails without attachments...")
            yettel_emails = await self.gmail_client.get_recent_emails_all(
                hours_back=hours_back,
                max_results=50,
                sender_filter="eszamla@yettel.hu"
            )
            print(f"📧 DEBUG: Found {len(yettel_emails)} Yettel emails")

            # Combine both lists and remove duplicates by email ID
            emails = emails_with_pdfs.copy()
            existing_ids = {email['id'] for email in emails}
            for yettel_email in yettel_emails:
                if yettel_email['id'] not in existing_ids:
                    emails.append(yettel_email)
                    existing_ids.add(yettel_email['id'])

            print(f"📧 DEBUG: Total {len(emails)} emails to process")
            print(f"Found {len(emails)} emails to process")
            
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

    async def process_labeled_emails(self) -> int:
        """Process emails tagged with ITC/Process-* labels (label-trigger mode).

        Each label maps to an invoice_type via settings.label_triggers. After a
        successful run the trigger label is removed and the processed_label is added.
        """
        settings = self.rules_engine.settings
        label_triggers = settings.get('label_triggers', {})
        processed_label = settings.get('processed_label')

        if not label_triggers:
            print("⚠️  No label_triggers configured in settings - nothing to do")
            return 0

        print(f"\\n🏷️  Label-trigger mode: scanning {len(label_triggers)} trigger label(s)")

        processed_count = 0
        for label_name, invoice_type in label_triggers.items():
            print(f"\\n🔎 Checking label: {label_name} → {invoice_type}")
            emails = await self.gmail_client.get_emails_by_label(label_name)
            print(f"   Found {len(emails)} email(s) with this label")

            for email in emails:
                success = await self.process_single_email(email, type_override=invoice_type)
                if success:
                    processed_count += 1
                    # Remove trigger label so it is not reprocessed; mark as processed
                    await self.gmail_client.remove_label(email['id'], label_name)
                    if processed_label:
                        await self.gmail_client.add_label(email['id'], processed_label)
                else:
                    print(f"   ⚠️  Failed - keeping label {label_name} on message {email['id']}")

        print(f"\\n✅ Label-trigger run complete: processed {processed_count} email(s)")
        return processed_count

    async def process_learn_emails(self) -> int:
        """Process emails tagged with ITC/Learn-* labels: interactively create a new
        partner rule from the email + PDF, persist it, then process the email.
        """
        import tempfile

        settings = self.rules_engine.settings
        learn_triggers = settings.get('learn_triggers', {})
        processed_label = settings.get('processed_label')

        if not learn_triggers:
            print("⚠️  No learn_triggers configured in settings - nothing to do")
            return 0

        print(f"\\n🎓 Learn mode: scanning {len(learn_triggers)} learn label(s)")

        learned = 0
        for label_name, invoice_type in learn_triggers.items():
            print(f"\\n🔎 Checking label: {label_name} → {invoice_type}")
            emails = await self.gmail_client.get_emails_by_label(label_name)
            print(f"   Found {len(emails)} email(s) with this label")

            for email in emails:
                pdfs = email.get('pdf_attachments', [])
                if not pdfs:
                    print(f"   ⚠️  No PDF on '{email['subject'][:40]}' - skipping")
                    continue

                att = pdfs[0]
                data = await self.gmail_client.download_attachment(
                    email['id'], att['attachment_id'], att['filename']
                )
                if not data:
                    print("   ⚠️  PDF download failed - skipping")
                    continue

                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tf:
                    tf.write(data)
                    tmp = Path(tf.name)
                pdf_text = self._extract_pdf_text(tmp)
                try:
                    tmp.unlink()
                except Exception:
                    pass

                partner = self._build_rule_interactively(email, pdf_text or "", invoice_type)
                if not partner:
                    print("   ⏭️  Skipped (no rule created)")
                    continue

                # Process the email with the freshly-created rule (validates patterns E2E)
                success = await self.process_single_email(email, type_override=invoice_type)
                if success:
                    learned += 1
                    await self.gmail_client.remove_label(email['id'], label_name)
                    if processed_label:
                        await self.gmail_client.add_label(email['id'], processed_label)
                else:
                    print(f"   ⚠️  Processing failed - keeping label {label_name}")

        print(f"\\n✅ Learn run complete: created/processed {learned} partner(s)")
        return learned

    def _build_rule_interactively(self, email: dict, pdf_text: str, invoice_type: str):
        """Interactively assemble + persist a new partner rule. Returns partner name or None."""
        import re
        import json as _json

        print("\\n" + "=" * 60)
        print("🎓 ÚJ PARTNER TANÍTÁSA")
        print("=" * 60)
        sender = email.get('sender', '')
        subject = email.get('subject', '')
        print(f"   Feladó: {sender}")
        print(f"   Tárgy:  {subject}")
        print(f"   PDF szöveg (első 400 karakter):\\n{pdf_text[:400]}\\n")

        # Parse sender address + display name
        m = re.search(r'<([^>]+)>', sender)
        addr = m.group(1).strip() if m else sender.strip()
        display = sender.split('<')[0].strip().strip('"') if '<' in sender else ''
        domain = addr.split('@')[-1] if '@' in addr else addr
        default_name = display or domain or 'Egyéb'

        name = input(f"   Partner neve [{default_name}]: ").strip() or default_name
        email_pat = input(f"   Email minta [{addr}]: ").strip() or addr
        subj_default = subject[:40]
        print("   Tárgy minta: Enter=email tárgya, '-'=nincs (csak feladó alapján), vagy írj sajátot")
        subj_in = input(f"   Tárgy minta [{subj_default}]: ").strip()
        if subj_in == '-':
            subj_pat = ''
        else:
            subj_pat = subj_in or subj_default
        prefix_default = (re.sub(r'[^A-Za-z0-9]', '', (display or domain).split('.')[0]).capitalize()[:12]) or 'Szamla'
        prefix = input(f"   Fájl prefix [{prefix_default}]: ").strip() or prefix_default
        desc = input(f"   Megjegyzés (Sheets) [{name}]: ").strip() or name

        # Amount: auto-detect via default_rule, then confirm
        tmp_class = self.rules_engine.fallback_classification(email, invoice_type)
        amt_hint = self.rules_engine.extract_amount(email, pdf_text, tmp_class)
        amt_in = input(f"   Összeg (HUF) [{int(amt_hint) if amt_hint else ''}]: ").strip()
        amount = None
        if amt_in:
            digits = re.sub(r'[^\\d]', '', amt_in.split(',')[0])
            amount = int(digits) if digits else None
        elif amt_hint:
            amount = int(amt_hint)

        amount_pattern = self._choose_pattern(
            "Összeg", pdf_text,
            self.rules_engine.generate_amount_pattern(pdf_text, amount) if amount else None,
            self.rules_engine.collect_amount_patterns())

        # Due date
        dd_in = input("   Esedékesség (YYYY-MM-DD, Enter=ma): ").strip()
        due_yyyymmdd = dd_in.replace('-', '') if dd_in else datetime.now().strftime('%Y%m%d')
        date_pattern = self._choose_pattern(
            "Dátum", pdf_text,
            self.rules_engine.generate_date_pattern(pdf_text, due_yyyymmdd),
            self.rules_engine.collect_date_patterns())

        rule = {
            'name': name,
            'email_patterns': [email_pat],
            'subject_patterns': [subj_pat] if subj_pat else [],
            'invoice_type': invoice_type,
            'payment_type': self.rules_engine._PAYMENT_TYPE_BY_INVOICE_TYPE.get(invoice_type, 'Vállalati számla'),
            'filename_prefix': prefix,
            'sheet_description': desc,
        }
        if amount_pattern:
            rule['amount_extraction'] = {'method': 'pdf', 'pdf_patterns': [amount_pattern]}
        if date_pattern:
            rule['due_date_extraction'] = {'pdf_patterns': [date_pattern]}

        print("\\n   ── ÚJ RULE ──")
        print(_json.dumps(rule, ensure_ascii=False, indent=2))
        ok = input("\\n   Mentés? (Y/n): ").strip().lower()
        if ok and ok != 'y':
            return None

        if self.rules_engine.create_partner_rule(rule):
            print(f"   ✅ Rule mentve: {name}")
            return name
        print("   ❌ Rule mentés sikertelen")
        return None

    def _choose_pattern(self, kind: str, pdf_text: str, generated, existing):
        """Offer the auto-generated pattern + any existing rule patterns that match
        this PDF; let the user pick by number, type a raw regex, then edit. Returns
        the chosen regex string or None.
        """
        import re

        options = []  # list of (description, pattern)
        seen = set()
        if generated:
            options.append(("auto-generált", generated))
            seen.add(generated)
        for pat in existing:
            if pat in seen:
                continue
            try:
                m = re.search(pat, pdf_text, re.IGNORECASE | re.MULTILINE)
            except re.error:
                continue
            if m:
                val = m.group(1) if m.groups() else m.group(0)
                options.append((f"létező, talált: {val}", pat))
                seen.add(pat)

        print(f"\\n   {kind} minta jelöltek:")
        if options:
            for i, (desc, pat) in enumerate(options, 1):
                print(f"     {i}. [{desc}] {pat}")
            print("     (szám = választ | üres = 1. | vagy írj be saját regexet | '-' = nincs)")
        else:
            print("     (nincs jelölt — írj be saját regexet, vagy '-'/üres = nincs)")

        choice = input(f"   {kind} választás: ").strip()
        if choice == '-':
            return None
        if not choice:
            chosen = options[0][1] if options else None
        elif choice.isdigit() and 1 <= int(choice) <= len(options):
            chosen = options[int(choice) - 1][1]
        else:
            chosen = choice  # treat as a raw regex

        if not chosen:
            return None
        edited = input(f"   Szerkeszthető [{chosen}]: ").strip()
        return edited or chosen

    async def process_single_email(self, email: dict, type_override: str = None) -> bool:
        """Process a single email through the complete workflow.

        type_override: when set (label-trigger mode), forces the invoice_type
        (e.g. 'kiadas_vallalati' / 'kiadas_penztár') regardless of partner defaults.
        """
        print(f"\\n📧 Processing: {email['subject'][:50]}...")
        print(f"   From: {email['sender']}")
        print(f"   PDFs: {len(email.get('pdf_attachments', []))}")
        
        try:
            # NEW: Check for duplicate processing first
            if self.sheets_client:
                print(f"   🔍 Checking if email {email['id']} was already processed...")
                processing_info = await self.sheets_client.is_email_already_processed(email['id'])

                if processing_info["processed"]:
                    decision = await self.sheets_client.should_reprocess_email(processing_info)
                    if not decision["should_reprocess"]:
                        print(f"   ⏭️  SKIPPED: {decision['reason']}")
                        return True  # Successfully handled by skipping
                    else:
                        print(f"   🔄 REPROCESSING: {decision['reason']}")
                else:
                    print(f"   ✨ NEW EMAIL: Not yet processed")

            # Check if email should be excluded
            is_excluded, exclusion_reason = self.rules_engine.is_excluded(email)
            if is_excluded:
                print(f"   🚫 EXCLUDED: {exclusion_reason}")
                print(f"   ⏭️  Skipping processing")
                return True  # Return True to indicate successful handling (by exclusion)
            
            # Classify email using rules engine
            classification = self.rules_engine.classify_email(email)

            if type_override:
                # Label-trigger mode: user picked the type explicitly
                if classification is None:
                    print(f"   🏷️  No partner rule matched - using fallback classification")
                    classification = self.rules_engine.fallback_classification(email, type_override)
                else:
                    classification = self.rules_engine.apply_type_override(classification, type_override)
            elif classification is None:
                # Auto mode: skip if no matching rule found
                print(f"   ⏭️  Skipping - no matching rule found")
                return True  # Return True to indicate successful handling (by skipping)

            print(f"   🏷️  Partner: {classification.partner_name} (confidence: {classification.confidence:.2f})")
            print(f"   🎯 Matched: {', '.join(classification.matched_patterns)}")
            print(f"   💰 Invoice Type: {classification.invoice_type}")
            print(f"   📁 Folder: {classification.folder_path}")

            # Check if this is a web-based PDF invoice
            if self.rules_engine.is_web_based_pdf(classification):
                print(f"   🌐 Web-based invoice detected - downloading from web link")
                success = await self.process_web_based_invoice(email, classification)
                if not success:
                    print(f"   ⚠️  Failed to process web-based invoice")
                return success

            # Process each PDF attachment (with filename filtering if specified)
            for attachment in email.get('pdf_attachments', []):
                # Check if this PDF should be processed based on filename patterns
                if self._should_process_pdf(attachment['filename'], classification):
                    success = await self.process_single_attachment(email, attachment, classification)
                    if not success:
                        print(f"   ⚠️  Failed to process attachment: {attachment['filename']}")
                else:
                    print(f"   ⏭️  Skipped attachment (filename filter): {attachment['filename']}")

            return True
            
        except Exception as e:
            print(f"   ❌ Error processing email: {e}")
            return False

    async def process_web_based_invoice(self, email: dict, classification) -> bool:
        """Process an invoice that requires downloading PDF from a web link"""
        print(f"   🌐 Processing web-based invoice: {email['subject'][:50]}...")

        try:
            # Get email body
            email_body = email.get('body', '')
            if not email_body:
                print(f"      ⚠️  No email body found")
                return False

            # Get the rule for this classification
            rule = self.rules_engine.rules.get(classification.partner_name)
            if not rule:
                print(f"      ⚠️  No rule found for {classification.partner_name}")
                return False

            # Step 1: Process web invoice (download PDF and extract web data)
            print(f"      1. Fetching invoice from web...")
            pdf_data, web_data, web_text = self.web_fetcher.process_web_invoice(email_body, rule)

            if not pdf_data:
                print(f"      ❌ Failed to download PDF from web")
                return False

            print(f"      ✅ Downloaded PDF from web ({len(pdf_data)} bytes)")

            # Step 2: Save PDF to Dropbox folder
            year = datetime.now().year
            dropbox_folder = Path(self.dropbox_client.settings.dropbox_sync_folder)
            target_folder = dropbox_folder / str(year) / classification.folder_path
            target_folder.mkdir(parents=True, exist_ok=True)

            # Generate filename from web data or use invoice number
            invoice_number = web_data.get('invoice_number_pattern')
            if invoice_number and isinstance(invoice_number, tuple):
                invoice_number = invoice_number[0] if len(invoice_number) > 0 else None

            filename = f"invoice_{invoice_number or datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
            temp_file_path = target_folder / filename

            with open(temp_file_path, 'wb') as f:
                f.write(pdf_data)

            print(f"      ✅ Saved PDF to: {temp_file_path.name}")

            # Step 3: Extract data from PDF
            extracted_amount = None
            extracted_eur_amount = None
            extracted_usd_amount = None
            due_date = None

            if classification.confidence > 0.5 and PDF_AVAILABLE:
                print(f"      2. Extracting data from PDF...")
                try:
                    pdf_text = self._extract_pdf_text(temp_file_path)

                    # Try web page text first (more reliable)
                    if web_text:
                        # Try to extract amount from web page text
                        extracted_amount = self.rules_engine.extract_amount_from_web(web_text, classification)
                        if extracted_amount:
                            print(f"      💰 Extracted amount from web: {extracted_amount:,.0f} HUF")

                        # Try to extract due date from web page text
                        due_date = self.rules_engine.extract_due_date_from_web(web_text, classification)
                        if due_date:
                            print(f"      📅 Extracted due date from web: {due_date}")

                    # Fallback to PDF extraction if web extraction failed
                    if not extracted_amount and pdf_text:
                        extracted_amount = self.rules_engine.extract_amount(email, pdf_text, classification)
                        if extracted_amount:
                            print(f"      💰 Extracted amount from PDF: {extracted_amount:,.0f} HUF")

                    if not due_date and pdf_text:
                        due_date = self.rules_engine.extract_due_date(pdf_text, classification)
                        if due_date:
                            print(f"      📅 Extracted due date from PDF: {due_date}")

                    # Use today's date if no due date found
                    if not due_date:
                        due_date = datetime.now().strftime("%Y%m%d")
                        print(f"      📅 Using today as due date: {due_date}")

                except Exception as e:
                    print(f"      ⚠️  Data extraction failed: {e}")
                    due_date = datetime.now().strftime("%Y%m%d")
            else:
                due_date = datetime.now().strftime("%Y%m%d")

            # Step 4: Rename file with date prefix and rule prefix
            local_file_path = self._rename_file_with_prefixes(temp_file_path, classification, due_date)
            print(f"      📝 Renamed to: {local_file_path.name}")

            # Step 5: File is already in final location
            dropbox_path = str(local_file_path)
            print(f"      ✅ File ready in: {dropbox_path}")

            # Step 6: Log to Google Sheets
            print(f"      3. Logging to Google Sheets...")

            # Get sheet description from rules
            sheet_description = rule.get('sheet_description', '')

            sheets_data = {
                'gmail_message_id': email['id'],
                'sender_email': email['sender'],
                'subject': email['subject'],
                'pdf_filename': local_file_path.name,
                'pdf_size_bytes': len(pdf_data),
                'local_path': str(local_file_path),
                'dropbox_link': dropbox_path or '',
                'processing_status': 'COMPLETED',
                'error_message': '',
                'extracted_amount': extracted_amount,
                'extracted_eur_amount': extracted_eur_amount,
                'extracted_usd_amount': extracted_usd_amount,
                'due_date': due_date,
                'sheet_description': sheet_description,
                'payment_type': classification.payment_type
            }

            sheets_success = await self.sheets_client.log_email_processing(sheets_data)
            if sheets_success:
                print(f"      ✅ Logged to Google Sheets")
            else:
                print(f"      ⚠️  Google Sheets logging failed")

            print(f"      🎉 Completed processing web-based invoice")
            return True

        except Exception as e:
            print(f"      ❌ Error processing web-based invoice: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def process_single_attachment(self, email: dict, attachment: dict, classification) -> bool:
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
            
            # Step 2: Save directly to final Dropbox location
            # Create target folder based on classification
            year = datetime.now().year
            dropbox_folder = Path(self.dropbox_client.settings.dropbox_sync_folder)
            
            if hasattr(classification, 'folder_override') and classification.folder_override == "berszamfejtes":
                # Special handling for bérszámfejtés: /ITCardigan/Cégiratok/Berpapirok/2025/08/
                month_num = self._extract_month_from_filename(filename, email)
                target_folder = dropbox_folder / "Cégiratok" / "Berpapirok" / str(year) / f"{month_num:02d}"
            else:
                # Regular invoices: /ITCardigan/2025/Bejövő or /ITCardigan/2025/Pénztár
                target_folder = dropbox_folder / str(year) / classification.folder_path
                
            target_folder.mkdir(parents=True, exist_ok=True)
            temp_file_path = target_folder / filename
            
            with open(temp_file_path, 'wb') as f:
                f.write(attachment_data)
            
            size_mb = len(attachment_data) / (1024*1024)
            print(f"      ✅ Downloaded locally ({size_mb:.1f} MB)")
            
            # Step 3: Extract amount and due date from PDF if it's a high-confidence match
            extracted_amount = None
            extracted_eur_amount = None
            extracted_usd_amount = None
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

                        # Extract USD amount if applicable
                        extracted_usd_amount = self.rules_engine.extract_usd_amount(email, pdf_text, classification)
                        if extracted_usd_amount:
                            print(f"      💵 Extracted USD amount: ${extracted_usd_amount:.2f} USD")

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
            
            # Step 5: File is already in final location, just set the path
            dropbox_path = str(local_file_path)
            print(f"      ✅ File ready in: {dropbox_path}")
            
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
                    'extracted_usd_amount': extracted_usd_amount,
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
            # Disabled: self._save_email_metadata(email_folder, email, attachment, dropbox_path, extracted_amount, due_date, local_file_path.name)
            
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
        from datetime import datetime
        try:
            filename = file_path.name
            
            # Use current month for Google Sheets logging (processing month, not payroll month)
            current_month = datetime.now().month
            year = datetime.now().year
            
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
                # Determine due date based on tax code - use current month for processing
                if 'NAV' in row['description'] or any(nav_code in row['tax_code'] for nav_code in ['2510', '2520', '2540']):
                    # NAV transfers should be 12th of current month
                    payroll_due_date = f"{year}{current_month:02d}12"
                else:
                    # Regular payroll entries should be 1st of current month
                    payroll_due_date = f"{year}{current_month:02d}01"
                
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
                    'extracted_usd_amount': None,
                    'due_date': payroll_due_date,
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
            # Exception: for bérszámfejtés files, keep original filename without date prefix
            original_name = original_path.name
            
            if classification.folder_override == "berszamfejtes":
                # For bérszámfejtés files, keep original filename
                new_name = original_name
            else:
                # For regular invoices, add date and rule prefix
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
    
    def _extract_month_from_filename(self, filename: str, email: dict) -> int:
        """Extract month number from filename or email date for bérszámfejtés files."""
        import re
        from datetime import datetime
        
        # Hungarian month names mapping
        hungarian_months = {
            'januar': 1, 'februor': 2, 'marzius': 3, 'aprilis': 4,
            'majus': 5, 'junius': 6, 'julius': 7, 'augusztus': 8,
            'szeptember': 9, 'oktober': 10, 'november': 11, 'december': 12
        }
        
        filename_lower = filename.lower()
        
        # Try to extract month from filename
        for month_name, month_num in hungarian_months.items():
            if month_name in filename_lower:
                return month_num
        
        # Try to extract from patterns like "2025Augusztus"
        for month_name, month_num in hungarian_months.items():
            pattern = rf'2025{month_name}'
            if re.search(pattern, filename_lower):
                return month_num
        
        # Fallback to email date or current month - 1 (since payroll is usually for previous month)
        try:
            # Parse email date and get previous month
            email_date = email.get('date', '')
            if email_date:
                # Parse email date format like "Thu, 4 Sep 2025 03:17:48 +0000"
                parsed_date = datetime.strptime(email_date.split(' +')[0], '%a, %d %b %Y %H:%M:%S')
                # Payroll is usually for previous month
                prev_month = parsed_date.month - 1 if parsed_date.month > 1 else 12
                return prev_month
        except:
            pass
            
        # Final fallback: current month - 1
        current_month = datetime.now().month
        return current_month - 1 if current_month > 1 else 12
    
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
    parser.add_argument("--mode", choices=["auto", "labels", "learn"], default="auto",
                        help="auto: time-based PDF scan (default); labels: process ITC/Process-* labeled emails; "
                             "learn: interactively create a partner rule from ITC/Learn-* labeled emails")
    
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
    
    if args.mode == "labels":
        print(f"\\n🏷️  Label-trigger mode")
        processed = await workflow.process_labeled_emails()
        print(f"\\n✅ Processed {processed} labeled emails")
    elif args.mode == "learn":
        print(f"\\n🎓 Learn mode (interactive)")
        learned = await workflow.process_learn_emails()
        print(f"\\n✅ Learned/processed {learned} partner(s)")
    elif args.once:
        print(f"\\n🔍 Single run mode - checking last {args.hours} hours")
        processed = await workflow.process_emails_once(hours_back=args.hours)
        print(f"\\n✅ Processed {processed} emails")
    else:
        await workflow.run_continuous(check_interval_minutes=args.interval)


if __name__ == "__main__":
    asyncio.run(main())