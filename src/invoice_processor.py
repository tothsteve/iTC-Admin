"""
Invoice Processing Rules Engine
Handles partner-specific email patterns, amount extraction, and classification
"""
import json
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class InvoiceClassification:
    """Result of invoice classification"""
    partner_name: str
    invoice_type: str  # kiadas_vallalati, kiadas_penztár, bevetel_vallalati
    payment_type: str  # Vállalati számla, Saját
    folder_path: str
    amount: Optional[float] = None
    currency: str = "HUF"
    confidence: float = 0.0
    matched_patterns: List[str] = field(default_factory=list)  # Which patterns matched
    folder_override: Optional[str] = None

class InvoiceRulesEngine:
    """Engine for processing invoices based on configurable rules"""
    
    def __init__(self, rules_file: str = "src/invoice_rules.json"):
        self.rules_file = Path(rules_file)
        self.rules = {}
        self.settings = {}
        self.load_rules()
    
    def load_rules(self) -> bool:
        """Load rules from JSON configuration file"""
        try:
            if not self.rules_file.exists():
                logger.error(f"Rules file not found: {self.rules_file}")
                return False
                
            with open(self.rules_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            self.rules = {rule['name']: rule for rule in config.get('rules', [])}
            self.exclusion_rules = config.get('exclusion_rules', [])
            self.default_rule = config.get('default_rule', {})
            self.settings = config.get('settings', {})
            
            logger.info(f"✅ Loaded {len(self.rules)} processing rules and {len(self.exclusion_rules)} exclusion rules")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load rules: {e}")
            return False
    
    def reload_rules(self) -> bool:
        """Reload rules from file (for live configuration updates)"""
        logger.info("🔄 Reloading invoice rules...")
        return self.load_rules()
    
    def is_excluded(self, email_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if email should be excluded from processing
        
        Args:
            email_data: Dictionary with 'sender', 'subject', 'body' keys
            
        Returns:
            Tuple of (is_excluded: bool, reason: str)
        """
        sender = email_data.get('sender', '').lower()
        subject = email_data.get('subject', '').lower()
        
        for exclusion_rule in self.exclusion_rules:
            # Check sender patterns
            email_patterns = exclusion_rule.get('email_patterns', [])
            for pattern in email_patterns:
                if pattern.lower() in sender:
                    # Also check subject patterns if specified
                    subject_patterns = exclusion_rule.get('subject_patterns', [])
                    if subject_patterns:
                        for subject_pattern in subject_patterns:
                            if subject_pattern.lower() in subject:
                                return True, f"Excluded by rule: {exclusion_rule['name']} (email: {pattern}, subject: {subject_pattern})"
                    else:
                        # No subject patterns specified, email pattern match is enough
                        return True, f"Excluded by rule: {exclusion_rule['name']} (email: {pattern})"
            
            # Check subject-only patterns
            subject_patterns = exclusion_rule.get('subject_patterns', [])
            if not email_patterns:  # Only if no email patterns were specified
                for subject_pattern in subject_patterns:
                    if subject_pattern.lower() in subject:
                        return True, f"Excluded by rule: {exclusion_rule['name']} (subject: {subject_pattern})"
        
        return False, ""
    
    def classify_email(self, email_data: Dict[str, Any]) -> InvoiceClassification:
        """
        Classify an email based on sender, subject, and content
        
        Args:
            email_data: Dictionary with 'sender', 'subject', 'body' keys
            
        Returns:
            InvoiceClassification with partner info and processing details
        """
        sender = email_data.get('sender', '').lower()
        subject = email_data.get('subject', '').lower()
        body = email_data.get('body', '').lower()
        
        # Try to match against known rules
        best_match = None
        best_score = 0.0
        best_matched_patterns = []
        
        # Get PDF count from email data
        pdf_count = len(email_data.get('attachments', []))
        
        for rule_name, rule in self.rules.items():
            score, matched_patterns = self._calculate_match_score(rule, sender, subject, body, pdf_count)
            
            if score > best_score:
                best_score = score
                best_match = rule
                best_matched_patterns = matched_patterns
                
        # Skip emails with low confidence (no general processing)
        if best_score < 0.5:  # Confidence threshold
            logger.info(f"⚠️  Skipping email with low confidence ({best_score:.2f}) - no matching rule found")
            return None
        else:
            rule = best_match
            rule_name = rule['name']
            confidence = best_score
            matched_patterns = best_matched_patterns
            
        # Create classification
        # Handle custom folder override
        folder_override = rule.get('folder_override')
        if folder_override:
            folder_path = self._get_folder_path(folder_override)
        else:
            folder_path = self._get_folder_path(rule.get('invoice_type', 'kiadas_vallalati'))
        
        classification = InvoiceClassification(
            partner_name=rule_name,
            invoice_type=rule.get('invoice_type', 'kiadas_vallalati'),
            payment_type=rule.get('payment_type', 'Vállalati számla'),
            folder_path=folder_path,
            confidence=confidence,
            matched_patterns=matched_patterns,
            folder_override=rule.get('folder_override')
        )
        
        logger.info(f"📋 Classified email as: {rule_name} ({classification.invoice_type}) - confidence: {confidence:.2f}")
        return classification
    
    def _calculate_match_score(self, rule: Dict, sender: str, subject: str, body: str, pdf_count: int = 0) -> Tuple[float, List[str]]:
        """Calculate how well a rule matches the email and return matched patterns"""
        score = 0.0
        total_checks = 0
        matched_patterns = []
        
        # Check sender patterns
        email_patterns = rule.get('email_patterns', [])
        if email_patterns:
            total_checks += 2  # Email match is worth 2 points
            for pattern in email_patterns:
                if pattern.lower() in sender:
                    score += 2
                    matched_patterns.append(f"email: {pattern}")
                    break
        
        # Check subject patterns  
        subject_patterns = rule.get('subject_patterns', [])
        if subject_patterns:
            total_checks += 1  # Subject match is worth 1 point
            for pattern in subject_patterns:
                if pattern.lower() in subject:
                    score += 1
                    matched_patterns.append(f"subject: {pattern}")
                    break
        
        # Check body patterns
        body_patterns = rule.get('body_patterns', [])
        if body_patterns:
            total_checks += 1  # Body match is worth 1 point
            for pattern in body_patterns:
                if pattern.lower() in body:
                    score += 1
                    matched_patterns.append(f"body: {pattern}")
                    break
        
        # Check PDF count requirement
        pdf_count_required = rule.get('pdf_count_required')
        if pdf_count_required:
            total_checks += 1  # PDF count match is worth 1 point
            if pdf_count == pdf_count_required:
                score += 1
                matched_patterns.append(f"pdf_count: {pdf_count}")
        
        # Return normalized score and matched patterns
        final_score = score / max(total_checks, 1) if total_checks > 0 else 0.0
        return final_score, matched_patterns
    
    def extract_amount(self, email_data: Dict[str, Any], pdf_text: str = "", classification: InvoiceClassification = None) -> Optional[float]:
        """
        Extract amount from email or PDF based on classification rules
        
        Args:
            email_data: Email content
            pdf_text: Extracted PDF text
            classification: Result from classify_email
            
        Returns:
            Amount as float or None if not found
        """
        if not classification:
            return None
            
        # Get the rule for this classification
        rule = self.rules.get(classification.partner_name)
        if not rule:
            rule = self.default_rule
            
        extraction_config = rule.get('amount_extraction', {})
        method = extraction_config.get('method', 'both')
        
        amount = None
        
        # Try email extraction first (faster)
        if method in ['email', 'both']:
            amount = self._extract_from_email(email_data, extraction_config.get('email_patterns', []))
            if amount:
                logger.info(f"💰 Extracted amount from email: {amount}")
                return amount
        
        # Try PDF extraction if email failed or method is PDF
        if method in ['pdf', 'both'] and pdf_text:
            amount = self._extract_from_pdf(pdf_text, extraction_config.get('pdf_patterns', []))
            if amount:
                logger.info(f"💰 Extracted amount from PDF: {amount}")
                return amount
        
        logger.warning(f"⚠️  Could not extract amount using method: {method}")
        return None
    
    def extract_eur_amount(self, email_data: Dict[str, Any], pdf_text: str = "", classification: InvoiceClassification = None) -> Optional[float]:
        """
        Extract EUR amount from PDF for dual currency invoices like Google Workspace
        
        Args:
            email_data: Email content
            pdf_text: Extracted PDF text
            classification: Result from classify_email
            
        Returns:
            EUR amount as float or None if not found
        """
        if not classification or not pdf_text:
            return None
            
        # Get the rule for this classification
        rule = self.rules.get(classification.partner_name)
        if not rule:
            return None
            
        # Check if rule has EUR extraction config
        eur_extraction = rule.get('amount_extraction', {}).get('eur_extraction', {})
        if not eur_extraction:
            return None
            
        pdf_patterns = eur_extraction.get('pdf_patterns', [])
        
        for pattern in pdf_patterns:
            try:
                matches = re.findall(pattern, pdf_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    match_result = matches[0]

                    # Handle multiple capture groups (tuples) vs single capture group (string)
                    if isinstance(match_result, tuple):
                        # Multiple capture groups - reMarkable format: ('2', '9', '9') -> "2.99"
                        if len(match_result) == 3:
                            amount_str = f"{match_result[0]}.{match_result[1]}{match_result[2]}"
                        elif len(match_result) == 2:
                            amount_str = f"{match_result[0]}.{match_result[1]}"
                        else:
                            # Fallback: join all groups
                            amount_str = ''.join(match_result)
                    else:
                        # Single capture group (string) - existing logic for Google/Anthropic
                        amount_str = match_result

                        # Special handling for Anthropic spaced format: "5 . 0 0" or "1 8 . 0 0" -> "5.00" or "18.00"
                        if re.search(r'\s', amount_str):
                            # If there are any spaces, remove them all
                            amount_str = re.sub(r'\s+', '', amount_str)
                        else:
                            # Standard European number format: 32.40 -> 32.40
                            amount_str = amount_str.replace(',', '')  # Remove thousands separators if any

                    eur_amount = float(amount_str)
                    logger.info(f"💶 Extracted EUR amount: {eur_amount}")
                    return eur_amount
            except Exception as e:
                logger.warning(f"EUR pattern '{pattern}' failed: {e}")
        
        logger.warning(f"⚠️  Could not extract EUR amount")
        return None

    def extract_usd_amount(self, email_data: Dict[str, Any], pdf_text: str = "", classification: InvoiceClassification = None) -> Optional[float]:
        """
        Extract USD amount from PDF for USD invoices like Railway Corporation

        Args:
            email_data: Email content
            pdf_text: Extracted PDF text
            classification: Result from classify_email

        Returns:
            USD amount as float or None if not found
        """
        if not classification or not pdf_text:
            return None

        # Get the rule for this classification
        rule = self.rules.get(classification.partner_name)
        if not rule:
            return None

        # Check if rule has USD extraction config
        usd_extraction = rule.get('amount_extraction', {}).get('usd_extraction', {})
        if not usd_extraction:
            return None

        pdf_patterns = usd_extraction.get('pdf_patterns', [])

        for pattern in pdf_patterns:
            try:
                matches = re.findall(pattern, pdf_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    match_result = matches[0]

                    # Handle multiple capture groups (tuples) vs single capture group (string)
                    if isinstance(match_result, tuple):
                        # Multiple capture groups
                        if len(match_result) == 3:
                            amount_str = f"{match_result[0]}.{match_result[1]}{match_result[2]}"
                        elif len(match_result) == 2:
                            amount_str = f"{match_result[0]}.{match_result[1]}"
                        else:
                            # Fallback: join all groups
                            amount_str = ''.join(match_result)
                    else:
                        # Single capture group (string)
                        amount_str = match_result
                        # USD uses standard format: XX.XX (no special conversion needed)
                        amount_str = amount_str.replace(',', '')  # Remove thousands separators if any

                    usd_amount = float(amount_str)
                    logger.info(f"💵 Extracted USD amount: ${usd_amount:.2f}")
                    return usd_amount
            except Exception as e:
                logger.warning(f"USD pattern '{pattern}' failed: {e}")

        logger.warning(f"⚠️  Could not extract USD amount")
        return None

    def _extract_from_email(self, email_data: Dict[str, Any], patterns: List[str]) -> Optional[float]:
        """Extract amount from email body using regex patterns"""
        email_text = f"{email_data.get('subject', '')} {email_data.get('body', '')}"
        
        for pattern in patterns:
            try:
                matches = re.findall(pattern, email_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    amount_str = matches[0]
                    # Clean up Hungarian number format: 1.234.567,89 -> 1234567.89
                    amount_str = amount_str.replace('.', '').replace(',', '.')
                    return float(amount_str)
            except Exception as e:
                logger.warning(f"Pattern '{pattern}' failed: {e}")
        
        return None
    
    def _extract_from_pdf(self, pdf_text: str, patterns: List[str]) -> Optional[float]:
        """Extract amount from PDF text using regex patterns"""
        for pattern in patterns:
            try:
                matches = re.findall(pattern, pdf_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    amount_str = matches[0]
                    # Clean up various Hungarian number formats
                    # Handle space-separated thousands with different decimal separators
                    if ' ' in amount_str:
                        # Check if it's like "3 548.94" (space thousands + period decimal)
                        if '.' in amount_str and amount_str.count('.') == 1:
                            # Split on last space to separate thousands from decimal part
                            parts = amount_str.rsplit(' ', 1)
                            if len(parts) == 2 and '.' in parts[1]:
                                # "3 548" + ".94" -> "3548.94"
                                amount_str = parts[0].replace(' ', '') + '.' + parts[1].split('.')[1]
                            else:
                                # Just remove spaces: "21 489" -> "21489"
                                amount_str = amount_str.replace(' ', '')
                        else:
                            # Handle "21 489,50" -> "21489.50"
                            amount_str = amount_str.replace(' ', '').replace(',', '.')
                    else:
                        # Handle dot-separated thousands (21.489,50 -> 21489.50)
                        amount_str = amount_str.replace('.', '').replace(',', '.')
                    return float(amount_str)
            except Exception as e:
                logger.warning(f"PDF pattern '{pattern}' failed: {e}")
        
        return None
    
    def _get_folder_path(self, invoice_type: str) -> str:
        """Get the target folder path for an invoice type"""
        base_folder = self.settings.get('base_folder', '/Users/tothi/Downloads/testinvoicecopy')
        current_year = self.settings.get('current_year', 2025)
        
        # Create year/type folder structure
        folder_mapping = self.settings.get('folder_structure', {})
        folder_name = folder_mapping.get(invoice_type, invoice_type)
        
        folder_path = Path(base_folder) / str(current_year) / folder_name
        return str(folder_path)

    # Payment type per invoice type (used by label-trigger overrides)
    _PAYMENT_TYPE_BY_INVOICE_TYPE = {
        'kiadas_vallalati': 'Vállalati számla',
        'kiadas_penztár': 'Saját',
        'bevetel_vallalati': 'Vállalati számla',
    }

    def payment_type_for(self, invoice_type: str) -> str:
        """Public accessor for the payment type of an invoice type."""
        return self._PAYMENT_TYPE_BY_INVOICE_TYPE.get(invoice_type, 'Vállalati számla')

    def apply_type_override(self, classification: "InvoiceClassification", invoice_type: str) -> "InvoiceClassification":
        """Force a specific invoice_type on an existing classification (label-trigger).

        Keeps partner detection (prefix, extraction patterns, sheet_description) intact but
        redirects the destination folder + payment type to the user-chosen type.
        """
        classification.invoice_type = invoice_type
        classification.folder_path = self._get_folder_path(invoice_type)
        classification.payment_type = self._PAYMENT_TYPE_BY_INVOICE_TYPE.get(
            invoice_type, classification.payment_type
        )
        logger.info(f"🏷️  Applied type override: {invoice_type} → folder {classification.folder_path}")
        return classification

    def fallback_classification(self, email_data: Dict[str, Any], invoice_type: str) -> "InvoiceClassification":
        """Build a minimal classification when no partner rule matches (label-trigger).

        Lets a manually-labeled email of an unknown partner still be processed with the
        chosen type, using the default_rule extraction patterns.
        """
        sender = email_data.get('sender', '')
        # Derive a readable partner name from the sender domain, else "Egyéb"
        partner_name = "Egyéb"
        if '@' in sender:
            domain = sender.split('@')[-1].strip('>').strip()
            if domain:
                partner_name = domain
        classification = InvoiceClassification(
            partner_name=partner_name,
            invoice_type=invoice_type,
            payment_type=self._PAYMENT_TYPE_BY_INVOICE_TYPE.get(invoice_type, 'Vállalati számla'),
            folder_path=self._get_folder_path(invoice_type),
            # >0.5 so the integrated_workflow PDF-extraction gate runs default_rule patterns
            confidence=0.6,
            matched_patterns=["label-trigger fallback"],
        )
        logger.info(f"🏷️  Fallback classification: {partner_name} ({invoice_type}) via label-trigger")
        return classification

    # ------------------------------------------------------------------
    # Rule learning (ITC/Learn-* labels): generate patterns + persist rule
    # ------------------------------------------------------------------

    # Matches a Hungarian-formatted amount: 1 234 567,89 / 1.234.567 / 12345
    _AMOUNT_NUM_RE = r'(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+)'

    def _amount_text_forms(self, amount: float) -> List[str]:
        """Plausible textual renderings of an integer amount, longest first."""
        n = int(round(amount))
        grouped_space = f"{n:,}".replace(",", " ")   # 1 234 567
        grouped_dot = f"{n:,}".replace(",", ".")      # 1.234.567
        forms = {str(n), grouped_space, grouped_dot}
        return sorted(forms, key=len, reverse=True)

    def generate_amount_pattern(self, pdf_text: str, amount: float) -> Optional[str]:
        """Build an amount-extraction regex by locating the confirmed amount + its label.

        Returns a regex with one capture group, or None if the amount is not found
        verbatim in the text.
        """
        if not amount or not pdf_text:
            return None
        for form in self._amount_text_forms(amount):
            idx = pdf_text.find(form)
            if idx == -1:
                continue
            prefix = pdf_text[max(0, idx - 50):idx]
            # Trailing label word(s) right before the number (e.g. "Fizetendő összeg:")
            label_match = re.search(r'([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű .]{2,30}?)\s*[:=]?\s*$', prefix)
            label = label_match.group(1).strip() if label_match else ''
            # Trailing currency (Ft / HUF) right after the number
            suffix = pdf_text[idx + len(form): idx + len(form) + 6]
            cur_match = re.search(r'^\s*(Ft|HUF)', suffix, re.IGNORECASE)
            currency = cur_match.group(1) if cur_match else ''
            pat = ''
            if label:
                pat += re.escape(label) + r'\s*[:=]?\s*'
            pat += self._AMOUNT_NUM_RE
            if currency:
                pat += r'\s*' + re.escape(currency)
            # Require at least a label or a currency anchor to avoid matching any number
            if label or currency:
                return pat
        return None

    def generate_date_pattern(self, pdf_text: str, due_date_yyyymmdd: str) -> Optional[str]:
        """Build a due-date regex from a confirmed YYYYMMDD date if it appears in the text.

        Returns a regex producing a capture group matching the date string, else None.
        """
        if not due_date_yyyymmdd or len(due_date_yyyymmdd) != 8 or not pdf_text:
            return None
        y, m, d = due_date_yyyymmdd[:4], due_date_yyyymmdd[4:6], due_date_yyyymmdd[6:8]
        # Common renderings of the date
        candidates = [
            (f"{y}.{m}.{d}", r'(\d{4}\.\d{2}\.\d{2})'),
            (f"{y}-{m}-{d}", r'(\d{4}-\d{2}-\d{2})'),
            (f"{y}. {int(m)}. {int(d)}", r'(\d{4}\.\s*\d{1,2}\.\s*\d{1,2})'),
            (f"{int(m)}/{int(d)}/{y}", r'(\d{1,2})/(\d{1,2})/(\d{4})'),
        ]
        for literal, num_re in candidates:
            idx = pdf_text.find(literal)
            if idx == -1:
                continue
            prefix = pdf_text[max(0, idx - 50):idx]
            label_match = re.search(r'([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű][A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű .]{2,30}?)\s*[:=]?\s*$', prefix)
            label = label_match.group(1).strip() if label_match else ''
            pat = (re.escape(label) + r'\s*[:=]?\s*' if label else '') + num_re
            return pat
        return None

    def collect_amount_patterns(self) -> List[str]:
        """All amount pdf_patterns across existing rules + default_rule (deduped)."""
        out = []
        for rule in list(self.rules.values()) + [getattr(self, 'default_rule', {})]:
            for pat in rule.get('amount_extraction', {}).get('pdf_patterns', []):
                if pat not in out:
                    out.append(pat)
        return out

    def collect_date_patterns(self) -> List[str]:
        """All due-date pdf_patterns across existing rules (deduped)."""
        out = []
        for rule in self.rules.values():
            for pat in rule.get('due_date_extraction', {}).get('pdf_patterns', []):
                if pat not in out:
                    out.append(pat)
        return out

    def create_partner_rule(self, rule: Dict[str, Any]) -> bool:
        """Append a new partner rule to memory and persist it to the rules JSON file.

        Writes a timestamp-free .bak backup of the previous file first.
        """
        name = rule.get('name')
        if not name:
            logger.error("Cannot create rule without a name")
            return False
        try:
            with open(self.rules_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Replace existing rule with same name, else append
            rules_list = config.get('rules', [])
            rules_list = [r for r in rules_list if r.get('name') != name]
            rules_list.append(rule)
            config['rules'] = rules_list

            backup = self.rules_file.with_suffix(self.rules_file.suffix + '.bak')
            with open(backup, 'w', encoding='utf-8') as f:
                json.dump({'rules': self.rules and list(self.rules.values()) or [],
                           'exclusion_rules': getattr(self, 'exclusion_rules', []),
                           'default_rule': getattr(self, 'default_rule', {}),
                           'settings': self.settings}, f, ensure_ascii=False, indent=2)

            with open(self.rules_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.write('\n')

            # Update in-memory rules
            self.rules[name] = rule
            logger.info(f"✅ Created and persisted partner rule: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to persist rule {name}: {e}")
            return False

    def get_google_sheets_config(self, invoice_type: str, year: int = None) -> Dict[str, str]:
        """Get Google Sheets configuration for invoice type and year"""
        if not year:
            year = self.settings.get('current_year', 2025)
            
        sheets_config = self.settings.get('google_sheets', {})
        
        return {
            'spreadsheet_id': sheets_config.get('spreadsheet_id'),
            'worksheet_name': sheets_config.get('worksheet_template', '{year}').format(year=year),
            'target_column': sheets_config.get('columns', {}).get(invoice_type, {}).get('target', 'Kiadás HUF')
        }
    
    def add_custom_rule(self, rule_data: Dict[str, Any]) -> bool:
        """Add a new custom rule (for dynamic rule creation)"""
        try:
            rule_name = rule_data.get('name')
            if not rule_name:
                logger.error("Rule must have a name")
                return False
                
            self.rules[rule_name] = rule_data
            logger.info(f"✅ Added custom rule: {rule_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add custom rule: {e}")
            return False
    
    def extract_due_date(self, pdf_text: str, classification: "InvoiceClassification" = None) -> Optional[str]:
        """
        Extract payment due date from PDF text
        
        Args:
            pdf_text: Extracted PDF text
            classification: Result from classify_email
            
        Returns:
            Due date in YYYYMMDD format or None if not found
        """
        if not classification or classification.partner_name == "Unknown Invoice":
            return None
            
        # Get the rule for this classification
        rule = None
        for rule_name, rule_config in self.rules.items():
            if rule_config['name'] == classification.partner_name:
                rule = rule_config
                break
                
        if not rule or 'due_date_extraction' not in rule:
            return None
            
        # Try PDF patterns
        due_date_config = rule['due_date_extraction']
        pdf_patterns = due_date_config.get('pdf_patterns', [])
        
        for pattern in pdf_patterns:
            matches = re.findall(pattern, pdf_text, re.IGNORECASE | re.MULTILINE)
            if matches:
                match = matches[0]
                logger.info(f"Found due date match with pattern '{pattern}': {match}")
                if isinstance(match, tuple):
                    # Handle different date formats
                    if len(match) == 3:
                        # Could be YYYY-MM-DD, DD-MM-YYYY, or MM/DD/YYYY format
                        part1, part2, part3 = match

                        # Special handling for month name formats
                        if classification and (classification.partner_name == "Anthropic" or classification.partner_name == "Google Workspace" or classification.partner_name == "reMarkable" or classification.partner_name == "Railway Corporation"):
                            if classification.partner_name == "Anthropic" and ' ' in part1:
                                # Anthropic spaced format: ("A u g u s t", "2 4", "2 0 2 5")
                                month_name = re.sub(r'\s+', '', part1)  # "A u g u s t" -> "August"
                                day = re.sub(r'\s+', '', part2)         # "2 4" -> "24"
                                year = re.sub(r'\s+', '', part3)        # "2 0 2 5" -> "2025"
                            elif (classification.partner_name == "reMarkable" or classification.partner_name == "Railway Corporation") and ' ' in part1:
                                # reMarkable/Railway spaced format: ("S e p t e m b e r", "1 9", "2 0 2 5")
                                month_name = re.sub(r'\s+', '', part1)  # "S e p t e m b e r" -> "September"
                                day = re.sub(r'\s+', '', part2)         # "1 9" -> "19"
                                year = re.sub(r'\s+', '', part3)        # "2 0 2 5" -> "2025"
                            elif classification.partner_name == "Google Workspace":
                                # Google standard format: ("Aug", "31", "2025")
                                month_name = part1  # "Aug"
                                day = part2         # "31"
                                year = part3        # "2025"
                            else:
                                month_name = day = year = None

                            if month_name and day and year:
                                # Convert month name to number (handle both full and abbreviated names)
                                month_map = {
                                    'January': '01', 'Jan': '01', 'February': '02', 'Feb': '02',
                                    'March': '03', 'Mar': '03', 'April': '04', 'Apr': '04',
                                    'May': '05', 'June': '06', 'Jun': '06', 'July': '07', 'Jul': '07',
                                    'August': '08', 'Aug': '08', 'September': '09', 'Sep': '09',
                                    'October': '10', 'Oct': '10', 'November': '11', 'Nov': '11',
                                    'December': '12', 'Dec': '12'
                                }
                                month = month_map.get(month_name, None)
                                if month:
                                    try:
                                        date_obj = datetime(int(year), int(month), int(day))
                                        return date_obj.strftime("%Y%m%d")
                                    except ValueError:
                                        continue
                        else:
                            # Standard date format handling
                            # Check if first part is year (4 digits)
                            if len(part1) == 4:  # YYYY-MM-DD format
                                year, month, day = part1, part2, part3
                            elif len(part3) == 4:  # Either DD-MM-YYYY or MM/DD/YYYY format
                                # Check if we can determine which format based on values
                                # If part1 > 12, it's likely DD-MM-YYYY
                                # If part1 <= 12 and part2 > 12, it's likely MM/DD/YYYY
                                if int(part1) > 12:  # DD-MM-YYYY format
                                    day, month, year = part1, part2, part3
                                elif int(part2) > 12:  # MM/DD/YYYY format
                                    month, day, year = part1, part2, part3
                                else:
                                    # Ambiguous case - try MM/DD/YYYY first for Spaces invoices
                                    # since the pattern order puts MM/DD/YYYY pattern first
                                    month, day, year = part1, part2, part3
                            else:
                                # Default fallback
                                day, month, year = part1, part2, part3

                            try:
                                # Validate and format date
                                date_obj = datetime(int(year), int(month), int(day))
                                return date_obj.strftime("%Y%m%d")
                            except ValueError:
                                continue
                else:
                    # Single match - try to parse as date
                    try:
                        # Assume it's already in good format
                        return match.replace('-', '').replace('.', '')
                    except:
                        continue
        
        return None

    def is_web_based_pdf(self, classification: InvoiceClassification = None) -> bool:
        """
        Check if this rule uses web-based PDF extraction

        Args:
            classification: Result from classify_email

        Returns:
            True if PDF should be downloaded from web, False otherwise
        """
        if not classification:
            return False

        rule = self.rules.get(classification.partner_name)
        if not rule:
            return False

        return rule.get('pdf_source') == 'web'

    def extract_amount_from_web(self, web_page_text: str, classification: InvoiceClassification = None) -> Optional[float]:
        """
        Extract amount from web page text

        Args:
            web_page_text: Plain text content from web page
            classification: Result from classify_email

        Returns:
            Amount as float or None if not found
        """
        if not classification:
            return None

        rule = self.rules.get(classification.partner_name)
        if not rule:
            return None

        extraction_config = rule.get('amount_extraction', {})
        web_patterns = extraction_config.get('web_patterns', [])

        for pattern in web_patterns:
            try:
                matches = re.findall(pattern, web_page_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    amount_str = matches[0]
                    # Clean up Hungarian number format
                    if ' ' in amount_str:
                        amount_str = amount_str.replace(' ', '').replace(',', '.')
                    else:
                        amount_str = amount_str.replace('.', '').replace(',', '.')

                    amount = float(amount_str)
                    logger.info(f"💰 Extracted amount from web: {amount}")
                    return amount
            except Exception as e:
                logger.warning(f"Web pattern '{pattern}' failed: {e}")

        logger.warning(f"⚠️  Could not extract amount from web page")
        return None

    def extract_due_date_from_web(self, web_page_text: str, classification: InvoiceClassification = None) -> Optional[str]:
        """
        Extract due date from web page text

        Args:
            web_page_text: Plain text content from web page
            classification: Result from classify_email

        Returns:
            Due date in YYYYMMDD format or None if not found
        """
        if not classification:
            return None

        rule = self.rules.get(classification.partner_name)
        if not rule or 'due_date_extraction' not in rule:
            return None

        due_date_config = rule['due_date_extraction']
        web_patterns = due_date_config.get('web_patterns', [])

        for pattern in web_patterns:
            try:
                matches = re.findall(pattern, web_page_text, re.IGNORECASE | re.MULTILINE)
                if matches:
                    match = matches[0]
                    logger.info(f"Found due date match from web: {match}")

                    if isinstance(match, tuple) and len(match) == 3:
                        year, month, day = match
                        try:
                            date_obj = datetime(int(year), int(month), int(day))
                            return date_obj.strftime("%Y%m%d")
                        except ValueError:
                            continue
                    else:
                        # Single match - try to parse
                        return match.replace('-', '').replace('.', '')
            except Exception as e:
                logger.warning(f"Web date pattern '{pattern}' failed: {e}")

        logger.warning(f"⚠️  Could not extract due date from web page")
        return None


# Factory function
def create_rules_engine(rules_file: str = None) -> InvoiceRulesEngine:
    """Create and initialize the rules engine"""
    if not rules_file:
        rules_file = "src/invoice_rules.json"
        
    engine = InvoiceRulesEngine(rules_file)
    return engine


# Example usage for testing
if __name__ == "__main__":
    engine = create_rules_engine()
    
    # Test classification
    test_email = {
        'sender': 'szamlakuldes@danubiusexpert.hu',
        'subject': 'Új számla érkezett',
        'body': 'Kedves Ügyfél! Új számla érkezett. Összesen: 125.000,50 Ft'
    }
    
    classification = engine.classify_email(test_email)
    print(f"Partner: {classification.partner_name}")
    print(f"Type: {classification.invoice_type}")
    print(f"Folder: {classification.folder_path}")
    
    # Test amount extraction
    amount = engine.extract_amount(test_email, "", classification)
    print(f"Amount: {amount}")