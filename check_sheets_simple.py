#!/usr/bin/env python3
"""
Simple script to check existing Google Sheets data using gspread.oauth()
"""

import sys
import os
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from config import get_settings
import gspread

def check_existing_sheets_data():
    """Check existing Google Sheets data structure."""
    try:
        settings = get_settings()
        print(f'🔍 Checking spreadsheet: {settings.sheets_spreadsheet_id}')
        
        # Use gspread's built-in OAuth (simpler approach)
        gc = gspread.oauth()
        spreadsheet = gc.open_by_key(settings.sheets_spreadsheet_id)
        
        print(f'✅ Connected to spreadsheet: {spreadsheet.title}')
        
        # List all worksheets
        worksheets = spreadsheet.worksheets()
        print(f'📋 Available worksheets ({len(worksheets)}):')
        for ws in worksheets:
            print(f'   - {ws.title} ({ws.row_count} rows, {ws.col_count} cols)')
        
        # Check for 2025 worksheet specifically
        try:
            ws_2025 = spreadsheet.worksheet('2025')
            print(f'\\n✅ Found 2025 worksheet:')
            print(f'   Dimensions: {ws_2025.row_count} rows × {ws_2025.col_count} columns')
            
            # Get headers
            headers = ws_2025.row_values(1) if ws_2025.row_count > 0 else []
            print(f'   Headers ({len(headers)}): {headers}')
            
            # Get all data to analyze structure
            all_data = ws_2025.get_all_values()
            
            # Count non-empty rows
            data_rows = []
            for i, row in enumerate(all_data):
                if any(cell.strip() for cell in row):
                    data_rows.append((i+1, row))  # Store row number and data
            
            print(f'   Non-empty rows: {len(data_rows)}')
            
            if len(data_rows) > 0:
                print(f'\\n📊 Data Structure Analysis:')
                
                # Show headers
                if len(data_rows) > 0:
                    header_row = data_rows[0][1]  # First row data
                    print(f'   Row 1 (Headers): {header_row}')
                
                # Show a few sample data rows
                sample_rows = min(3, len(data_rows) - 1)  # Skip header, show max 3 data rows
                if sample_rows > 0:
                    print(f'\\n   Sample data rows:')
                    for i in range(1, sample_rows + 1):  # Start from row 2 (index 1)
                        if i < len(data_rows):
                            row_num, row_data = data_rows[i]
                            # Truncate long values for display
                            display_row = []
                            for cell in row_data:
                                if len(str(cell)) > 25:
                                    display_row.append(str(cell)[:25] + '...')
                                else:
                                    display_row.append(str(cell))
                            print(f'   Row {row_num}: {display_row}')
                
                # Show last row if there are many rows
                if len(data_rows) > 4:  # More than header + 3 samples
                    last_row_num, last_row_data = data_rows[-1]
                    display_last = []
                    for cell in last_row_data:
                        if len(str(cell)) > 25:
                            display_last.append(str(cell)[:25] + '...')
                        else:
                            display_last.append(str(cell))
                    print(f'   Row {last_row_num} (Last): {display_last}')
                
                # Calculate next append position
                next_row = len(data_rows) + 1
                print(f'\\n📝 Next data will be appended at row: {next_row}')
                
                # Analyze column structure for our invoice data
                print(f'\\n🔍 Column Analysis for Invoice Processing:')
                for i, header in enumerate(header_row):
                    print(f'   Column {i+1}: "{header}"')
                
            else:
                print(f'   📝 Worksheet is empty - will create new structure')
                
        except gspread.WorksheetNotFound:
            print(f'\\n❌ 2025 worksheet not found')
            print(f'Available worksheets: {[ws.title for ws in worksheets]}')
            
            # Check other worksheets for invoice data
            for ws in worksheets:
                if 'invoice' in ws.title.lower() or 'processing' in ws.title.lower():
                    print(f'\\n🔍 Found related worksheet: {ws.title}')
                    headers = ws.row_values(1)
                    print(f'   Headers: {headers}')
        
        return True
        
    except Exception as e:
        print(f'❌ Error: {e}')
        return False

if __name__ == "__main__":
    print("🔍 Analyzing Google Sheets Data Structure")
    print("=" * 50)
    
    success = check_existing_sheets_data()
    
    if success:
        print("\\n✅ Analysis complete!")
    else:
        print("\\n❌ Analysis failed!")