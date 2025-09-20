# Working State Backup - Before Duplicate Prevention Feature

## System State at Branch Creation
- **Branch:** main
- **Commit:** ea8b7e2 - Add Alza invoice processing support
- **Date:** 2025-09-17
- **Status:** ✅ FULLY OPERATIONAL per CLAUDE.md

## Known Working Commands

### Tested Working Command:
```bash
cd /Users/tothi/Workspace/ITCardigan/git/iTC-Admin
source venv/bin/activate
python scripts/integrated_workflow.py --hours 168 --once
```

### Last Successful Test Results (from CLAUDE.md):
- **Date:** September 9, 2025
- **Success Rate:** 100% (5/5 invoices processed perfectly)
- **Partners Tested:** Cleango, Schönherz, Danubius Expert, Spaces (2 invoices)

## Critical Working Components:
1. **Gmail OAuth2** - Working with credentials in .env
2. **Google Sheets Integration** - "Szamlak" spreadsheet, "2025" worksheet
3. **Partner Rules Engine** - 5 active partners (including new Alza)
4. **File Management** - Dropbox sync to `/Users/tothi/Dropbox/ITCardigan`
5. **PDF Processing** - Amount/date extraction working 100%

## Rollback Instructions:
```bash
cd /Users/tothi/Workspace/ITCardigan/git/iTC-Admin
git checkout main
git branch -D feature/duplicate-prevention  # if needed
source venv/bin/activate
python scripts/integrated_workflow.py --hours 24 --once  # test
```

## Current Google Sheets Schema (Before Changes):
- Column A: Dátum (Due date)
- Column B: Fizetve (Payment type)
- Column C: Bevétel HUF (Income)
- Column D: Kiadás HUF (Expense)
- Column E: Bevétel EUR
- Column F: Kiadás EUR
- Column G: Megjegyzés (Description)
- Column H: Link a számlára (File path)
- Column I: Column2 (Empty)

## Environment Requirements:
- Virtual environment MUST be activated
- .env file with Gmail/Sheets credentials required
- Dropbox sync folder must exist: `/Users/tothi/Dropbox/ITCardigan`