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
            matched_patterns=matched_patterns
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
                    amount_str = matches[0]
                    # Clean up European number format: 32.40 -> 32.40
                    amount_str = amount_str.replace(',', '')  # Remove thousands separators if any
                    eur_amount = float(amount_str)
                    logger.info(f"💶 Extracted EUR amount: {eur_amount}")
                    return eur_amount
            except Exception as e:
                logger.warning(f"EUR pattern '{pattern}' failed: {e}")
        
        logger.warning(f"⚠️  Could not extract EUR amount")
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