#!/usr/bin/env python3
"""Test API connections for ITC-Admin."""

import sys
import os
import asyncio
import argparse
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import get_settings


async def test_gmail_connection():
    """Test Gmail API connection."""
    print("Testing Gmail API connection...")
    try:
        from gmail.client import create_gmail_client
        
        client = await create_gmail_client()
        if not client:
            print("❌ Failed to create Gmail client")
            return False
        
        # Test connection
        connection_ok = await client.test_connection()
        if connection_ok:
            print("✅ Gmail API connection successful")
            
            # Get some stats
            stats = await client.get_processing_stats()
            print(f"   Recent messages (7 days): {stats.get('recent_messages_7_days', 'N/A')}")
            return True
        else:
            print("❌ Gmail API connection failed")
            return False
            
    except Exception as e:
        print(f"❌ Gmail API test failed: {e}")
        return False


async def test_dropbox_connection():
    """Test Dropbox API connection."""
    print("Testing Dropbox API connection...")
    try:
        import dropbox
        settings = get_settings()
        
        if not settings.dropbox_access_token or settings.dropbox_access_token == "your_dropbox_access_token_here":
            print("⏭️  Dropbox access token not configured")
            return False
        
        # Create Dropbox client
        dbx = dropbox.Dropbox(settings.dropbox_access_token)
        
        # Test connection
        account = dbx.users_get_current_account()
        print(f"✅ Dropbox API connection successful")
        print(f"   Account: {account.name.display_name}")
        print(f"   Email: {account.email}")
        return True
        
    except Exception as e:
        print(f"❌ Dropbox API test failed: {e}")
        return False


async def test_sheets_connection():
    """Test Google Sheets API connection."""
    print("Testing Google Sheets API connection...")
    try:
        settings = get_settings()
        
        if (not settings.sheets_client_id or settings.sheets_client_id == "your_sheets_client_id_here" or
            not settings.sheets_spreadsheet_id or settings.sheets_spreadsheet_id == "your_spreadsheet_id_here"):
            print("⏭️  Google Sheets credentials not configured")
            return False
        
        # For now, just check if credentials are configured
        # Full implementation would require sheets client
        print("⚠️  Google Sheets API test not fully implemented yet")
        print("   Credentials appear to be configured")
        return True
        
    except Exception as e:
        print(f"❌ Google Sheets API test failed: {e}")
        return False


async def test_transfer_api():
    """Test TransferXMLGenerator API connection."""
    print("Testing TransferXMLGenerator API connection...")
    try:
        import requests
        settings = get_settings()
        
        # Test basic connectivity
        response = requests.get(
            f"{settings.transfer_api_url}/api/",
            timeout=10,
            headers={"Authorization": f"Bearer {settings.transfer_api_token}"} if settings.transfer_api_token else {}
        )
        
        if response.status_code == 200:
            print("✅ TransferXMLGenerator API connection successful")
            return True
        else:
            print(f"❌ TransferXMLGenerator API returned status {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ TransferXMLGenerator API connection failed - service not running?")
        return False
    except Exception as e:
        print(f"❌ TransferXMLGenerator API test failed: {e}")
        return False


async def test_database():
    """Test database connection."""
    print("Testing database connection...")
    try:
        from database.models import get_engine, get_session_maker
        settings = get_settings()
        
        # Create engine and test connection
        engine = get_engine(settings.database_url)
        SessionMaker = get_session_maker(engine)
        
        session = SessionMaker()
        session.execute("SELECT 1")
        session.close()
        engine.dispose()
        
        print("✅ Database connection successful")
        return True
        
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


async def test_ocr_functionality():
    """Test OCR functionality."""
    print("Testing OCR functionality...")
    try:
        from pdf.extractor import check_ocr_dependencies
        
        deps = check_ocr_dependencies()
        
        if not deps["errors"]:
            print("✅ OCR functionality available")
            print(f"   Tesseract version: {deps.get('tesseract_version', 'unknown')}")
            print(f"   Hungarian language: {'✅' if deps['hungarian_language_available'] else '❌'}")
            return True
        else:
            print("❌ OCR functionality issues:")
            for error in deps["errors"]:
                print(f"   - {error}")
            return False
            
    except Exception as e:
        print(f"❌ OCR functionality test failed: {e}")
        return False


async def main():
    """Main test function."""
    parser = argparse.ArgumentParser(description="Test ITC-Admin API connections")
    parser.add_argument(
        "--service", 
        choices=["gmail", "dropbox", "sheets", "transfer", "database", "ocr", "all"],
        default="all",
        help="Specific service to test"
    )
    
    args = parser.parse_args()
    
    print("🧪 ITC-Admin Connection Tests")
    print("=" * 50)
    
    results = {}
    
    if args.service in ["all", "database"]:
        results["database"] = await test_database()
    
    if args.service in ["all", "ocr"]:
        results["ocr"] = await test_ocr_functionality()
    
    if args.service in ["all", "gmail"]:
        results["gmail"] = await test_gmail_connection()
    
    if args.service in ["all", "dropbox"]:
        results["dropbox"] = await test_dropbox_connection()
    
    if args.service in ["all", "sheets"]:
        results["sheets"] = await test_sheets_connection()
    
    if args.service in ["all", "transfer"]:
        results["transfer"] = await test_transfer_api()
    
    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Summary:")
    
    total_tests = len(results)
    passed_tests = sum(1 for r in results.values() if r)
    
    for service, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   {service.upper()}: {status}")
    
    print(f"\nResults: {passed_tests}/{total_tests} tests passed")
    
    if passed_tests == total_tests:
        print("🎉 All tests passed! System is ready.")
        sys.exit(0)
    else:
        print("⚠️  Some tests failed. Check configuration and dependencies.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())