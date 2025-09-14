#!/usr/bin/env python3
"""
Simple script to get data from Google Sheet 2025 worksheet.
Separate OAuth for Google Sheets only.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import gspread
from google.oauth2.service_account import Credentials
from config import get_settings

def get_2025_sheet_data():
    """Get data from 2025 worksheet."""
    try:
        settings = get_settings()
        print(f"📊 Connecting to spreadsheet: {settings.sheets_spreadsheet_id}")
        
        # Simple gspread oauth - this will create credentials automatically
        gc = gspread.oauth(
            scopes=['https://www.googleapis.com/auth/spreadsheets'],
            credentials_filename='data/credentials/gspread_credentials.json',
            authorized_user_filename='data/credentials/gspread_token.json'
        )
        
        # Open spreadsheet
        spreadsheet = gc.open_by_key(settings.sheets_spreadsheet_id)
        print(f"✅ Connected to: {spreadsheet.title}")
        
        # Get 2025 worksheet
        worksheet = spreadsheet.worksheet('2025')
        print(f"✅ Found 2025 worksheet")
        print(f"   Dimensions: {worksheet.row_count} rows × {worksheet.col_count} columns")
        
        # Get all data
        all_data = worksheet.get_all_values()
        print(f"   Raw data rows: {len(all_data)}")
        
        # Filter non-empty rows
        data_rows = []
        for i, row in enumerate(all_data):
            if any(cell.strip() for cell in row):
                data_rows.append((i+1, row))
        
        print(f"   Rows with data: {len(data_rows)}")
        
        if len(data_rows) > 0:
            print(f"\n📋 DATA STRUCTURE:")
            
            # Show headers
            headers = data_rows[0][1]
            print(f"Headers (Row 1): {headers}")
            
            # Show sample data rows
            sample_count = min(3, len(data_rows) - 1)
            if sample_count > 0:
                print(f"\nSample Data:")
                for i in range(1, sample_count + 1):
                    if i < len(data_rows):
                        row_num, row_data = data_rows[i]
                        truncated = [str(cell)[:30]+"..." if len(str(cell)) > 30 else str(cell) for cell in row_data]
                        print(f"  Row {row_num}: {truncated}")
            
            # Show last row if many rows
            if len(data_rows) > 4:
                last_row_num, last_row_data = data_rows[-1]
                truncated = [str(cell)[:30]+"..." if len(str(cell)) > 30 else str(cell) for cell in last_row_data]
                print(f"  Last Row {last_row_num}: {truncated}")
            
            print(f"\n📝 SUMMARY:")
            print(f"  - Total rows with data: {len(data_rows)}")
            print(f"  - Headers: {len(headers)} columns")
            print(f"  - Next available row for appending: {len(data_rows) + 1}")
            
            return {
                'success': True,
                'total_rows': len(data_rows),
                'headers': headers,
                'next_row': len(data_rows) + 1,
                'sample_data': data_rows[:5]  # First 5 rows including headers
            }
        else:
            print("📝 Worksheet appears empty")
            return {'success': True, 'total_rows': 0, 'headers': [], 'next_row': 1}
        
    except gspread.WorksheetNotFound:
        print("❌ ERROR: 2025 worksheet not found")
        return {'success': False, 'error': '2025 worksheet not found'}
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return {'success': False, 'error': str(e)}

if __name__ == "__main__":
    print("📊 Getting Google Sheets 2025 Data")
    print("=" * 50)
    
    result = get_2025_sheet_data()
    
    if result['success']:
        print(f"\n✅ SUCCESS!")
        print(f"Ready to append new invoice data at row {result.get('next_row', 'unknown')}")
    else:
        print(f"\n❌ FAILED: {result.get('error', 'unknown error')}")