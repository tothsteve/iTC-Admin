# Manual Invoice Processor

Command-line tool for processing individual PDF invoices outside of the Gmail email workflow.

## Quick Start

```bash
# Activate virtual environment (REQUIRED)
cd /Users/tothi/Workspace/ITCardigan/git/iTC-Admin
source venv/bin/activate

# Process a PDF invoice
python scripts/manual_invoice_processor.py ~/Downloads/invoice.pdf
```

## Usage Examples

```bash
# Auto-detect partner from PDF content
python scripts/manual_invoice_processor.py invoice.pdf

# Specify partner name (skip auto-detection)
python scripts/manual_invoice_processor.py invoice.pdf --partner "Danubius Expert"

# Test extraction without copying/logging (dry-run)
python scripts/manual_invoice_processor.py invoice.pdf --dry-run

# Use --pdf flag (alternative syntax)
python scripts/manual_invoice_processor.py --pdf invoice.pdf
```

## Features

### 🔍 Smart Partner Detection
- Automatically analyzes PDF content and filename
- Matches against 13+ configured business partner rules
- Shows confidence score and matched patterns
- Lets you confirm or select from full partner list

### 💰 Comprehensive Data Extraction
Extracts using partner-specific patterns:
- **Amount** (HUF/EUR/USD)
- **Due Date** (multiple formats supported)
- **Invoice Date** (for filename prefix)
- **Invoice Number** (from filename or PDF)
- **Partner Name**

### ✅ Interactive Confirmation
- Displays all extracted data in formatted table
- Prompts to confirm or edit each field
- Validates input (dates, amounts, required fields)
- Press Enter to accept default values

### 🚫 Duplicate Prevention
- Checks Google Sheets for existing invoice
- Searches by invoice number and filename
- Shows duplicate details if found
- Lets you decide to proceed or cancel

### 📦 Complete Processing
- Copies to Dropbox: `YYYYMMDD_Prefix_Original.pdf`
- Uses same folder structure as email processing
- Logs to Google Sheets with proper formatting
- Marks as "Verified (Manual)" in processing notes

## Workflow

1. **Initialize** - Connects to Google Sheets and Dropbox
2. **Extract Text** - Reads PDF content using PyPDF2
3. **Detect Partner** - Auto-classifies or lets you select
4. **Extract Data** - Uses partner-specific extraction patterns
5. **Confirm** - Interactive confirmation of all fields
6. **Check Duplicates** - Searches Google Sheets
7. **Copy to Dropbox** - Organized folder structure
8. **Log to Sheets** - Appends to existing data
9. **Summary** - Displays processing results

## Interactive Session Example

```
🚀 Initializing invoice processor...

✅ Loaded 13 partner rules
✅ Connected to Google Sheets
✅ Initialized Dropbox sync folder

📄 Processing PDF: invoice.pdf
📝 Extracting text from PDF...
✅ Extracted 2,145 characters from 2 pages

🔍 Auto-detecting business partner...
   Found potential match: Danubius Expert
   Confidence: 0.85

❓ Is this correct? (Y/n/show-all): y

💰 Extracting invoice data...

============================================================
📋 EXTRACTED DATA
============================================================
   Partner Name:    Danubius Expert
   Amount (HUF):    61,976 HUF
   Due Date:        2025-09-12
   Invoice Date:    2025-09-01
   Invoice Number:  KI2501065
============================================================

❓ Confirm extracted data:
   Partner Name (Danubius Expert): [Enter]
   Amount (HUF) (61976): [Enter]
   Due Date (YYYY-MM-DD) (2025-09-12): [Enter]
   Invoice Date (YYYY-MM-DD) (2025-09-01): [Enter]
   Invoice Number (KI2501065): [Enter]

🔍 Checking for duplicates...
✅ No duplicate found

📁 New filename: 20250901_Könyvelés_KI2501065_ITCardiganKft.pdf
📂 Copying to Dropbox...
✅ File copied successfully

📊 Logging to Google Sheets...
✅ Successfully logged

============================================================
✅ PROCESSING COMPLETE
============================================================
   Partner:      Danubius Expert
   Amount:       61,976 HUF
   Due Date:     20250912
   Invoice #:    KI2501065
   Dropbox:      /Users/tothi/Dropbox/ITCardigan/2025/Bejövő/...
============================================================
```

## Use Cases

1. **Vendor Portal Downloads** - Process invoices downloaded from websites
2. **Scanned Documents** - Process paper invoices scanned to PDF
3. **Forwarded Files** - Process PDFs from messenger/file sharing
4. **Failed Auto-Processing** - Manually process emails that failed
5. **Historical Data** - Backfill Google Sheets with old invoices

## Requirements

- ✅ Virtual environment activated
- ✅ Google Sheets credentials configured
- ✅ Dropbox sync folder set up
- ✅ Partner rules loaded from `invoice_rules.json`
- ✅ PyPDF2 installed (`pip install PyPDF2`)

## Supported Partners

Works with all 13+ configured business partners:
- Danubius Expert (Accounting)
- Schönherz (Student Cooperative)
- Spaces (Office Rental)
- Google Workspace
- Anthropic
- reMarkable
- Yettel
- Cleango
- Alza
- Tárhely.Eu
- And more...

## Technical Details

### Reuses Existing Components
- `InvoiceRulesEngine` - Partner classification and extraction
- `SheetsClient` - Google Sheets logging
- `LocalDropboxManager` - File copying
- Partner rules from `invoice_rules.json`

### Data Flow
```
PDF File
  → Extract Text (PyPDF2)
  → Classify Partner (Rules Engine)
  → Extract Data (Partner Patterns)
  → Confirm with User (Interactive)
  → Check Duplicates (Google Sheets)
  → Copy to Dropbox (Organized Folders)
  → Log to Sheets (Append Row)
  → Display Summary
```

### Duplicate Tracking
Uses special Gmail Message ID format:
```
manual_{invoice_number}_{timestamp}
```

Example: `manual_KI2501065_20251025143022`

### Verification Status
Marked as "Verified (Manual)" in Google Sheets to distinguish from automated email processing.

## Troubleshooting

### Import Errors
```bash
# Ensure virtual environment is activated
source venv/bin/activate

# Verify dependencies
pip list | grep PyPDF2
```

### Google Sheets Connection Failed
```bash
# Check credentials
ls ~/.config/gspread/

# Test connection
python scripts/test_connection.py --service sheets
```

### Dropbox Copy Failed
```bash
# Check folder exists
ls -la /Users/tothi/Dropbox/ITCardigan/

# Check permissions
touch /Users/tothi/Dropbox/ITCardigan/test.txt
```

### No Partner Match
- Use `--partner "Partner Name"` to specify manually
- Check PDF is readable (not image-only)
- Verify partner exists in `src/invoice_rules.json`

## Command-Line Options

```
usage: manual_invoice_processor.py [-h] [--pdf PDF_PATH_ALT]
                                   [--partner PARTNER] [--dry-run]
                                   [pdf_path]

positional arguments:
  pdf_path            Path to PDF invoice file

options:
  -h, --help          Show help message and exit
  --pdf PDF_PATH_ALT  Path to PDF invoice file (alternative flag)
  --partner PARTNER   Partner name (skip auto-detection)
  --dry-run           Test extraction without copying/logging
```

## Exit Codes

- `0` - Success
- `1` - Error (file not found, processing failed, user cancelled)

## See Also

- **CLAUDE.md** - Full system documentation
- **src/invoice_rules.json** - Partner configuration
- **scripts/integrated_workflow.py** - Automated email processing
- **scripts/test_connection.py** - Connection testing

---

**Status**: ✅ Fully implemented and tested
**Created**: October 25, 2025
**Author**: ITC-Admin System
