#!/usr/bin/env python3
"""
Simple script to find danubiusexpert.hu emails using existing Gmail client
"""
import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from gmail.client import create_gmail_client

async def find_danubius_emails():
    """Find all emails from danubiusexpert.hu"""
    print("🔍 Looking for danubiusexpert.hu emails")
    print("=" * 40)
    
    try:
        # Connect to Gmail
        gmail_client = await create_gmail_client()
        if not gmail_client:
            print("❌ Failed to connect to Gmail")
            return
        
        print("✅ Gmail connected")
        
        # Search specifically for szamlakuldes@danubiusexpert.hu
        print(f"\n🔍 Searching specifically for: szamlakuldes@danubiusexpert.hu")
        
        # Try different time ranges to find the email
        time_ranges = [24, 72, 168, 720, 2160, 8760]  # 1 day, 3 days, 1 week, 1 month, 3 months, 1 year
        
        for hours_back in time_ranges:
            days = hours_back // 24
            print(f"\n🕐 Checking last {days} days ({hours_back} hours)...")
            
            # Search ALL emails (not just with PDFs) for szamlakuldes@danubiusexpert.hu
            emails = await gmail_client.get_recent_emails_all(
                hours_back=hours_back,
                max_results=200,
                sender_filter="szamlakuldes@danubiusexpert.hu"
            )
            
            print(f"📨 Found {len(emails)} emails from szamlakuldes@danubiusexpert.hu")
            
            if not emails:
                continue
            
            # Check each email for danubius
            danubius_count = 0
            
            print("🔍 Scanning for danubiusexpert.hu...")
            
            for i, email in enumerate(emails):
                sender = email.get('sender', '').lower()
                subject = email.get('subject', '')
                
                # Print every 10th email to show progress
                if i % 10 == 0:
                    print(f"   Checked {i+1}/{len(emails)} emails...")
                
                # Check for exact sender match
                if 'szamlakuldes@danubiusexpert.hu' in sender:
                    danubius_count += 1
                    print(f"\n   ✅ FOUND EXACT SENDER MATCH #{danubius_count}:")
                    print(f"      From: {email.get('sender', 'Unknown')}")
                    print(f"      Subject: {email.get('subject', 'Unknown')}")
                    print(f"      Date: {email.get('date', 'Unknown')}")
                    print(f"      ID: {email.get('id', 'Unknown')}")
                    
                    # Show PDF attachments
                    pdfs = email.get('pdf_attachments', [])
                    print(f"      PDFs ({len(pdfs)}):")
                    for pdf in pdfs:
                        print(f"        - {pdf.get('filename', 'Unknown')}")
                    print()
                
                # Also check for any danubiusexpert.hu domain
                elif 'danubiusexpert.hu' in sender:
                    danubius_count += 1
                    print(f"\n   🔍 FOUND DOMAIN MATCH #{danubius_count}:")
                    print(f"      From: {email.get('sender', 'Unknown')}")
                    print(f"      Subject: {email.get('subject', 'Unknown')}")
                    print(f"      Date: {email.get('date', 'Unknown')}")
                    print()
            
            if danubius_count > 0:
                print(f"\n🎉 SUCCESS! Found {danubius_count} danubius emails in last {days} days")
                return danubius_count
            else:
                print(f"   ❌ No danubius emails found in last {days} days")
        
        print(f"\n❌ No danubius emails found in any time range")
        print(f"💡 Possible reasons:")
        print(f"   - Email is older than 3 months")
        print(f"   - Email doesn't have PDF attachments") 
        print(f"   - Domain is different (not danubiusexpert.hu)")
        print(f"   - Email is in a different folder/label")
        
        # Show some sample senders for comparison
        print(f"\n📋 Sample senders from recent emails:")
        recent_emails = await gmail_client.get_recent_emails_with_attachments(hours_back=168, max_results=20)
        for email in recent_emails[:10]:
            print(f"   - {email.get('sender', 'Unknown')}")
        
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def main():
    """Main function"""
    print("🎯 Finding Danubius Expert Emails")
    print("=" * 50)
    
    count = await find_danubius_emails()
    
    if count > 0:
        print(f"\n✅ Ready to proceed with {count} danubius emails!")
    else:
        print(f"\n❌ Need to investigate why danubius emails aren't found")

if __name__ == "__main__":
    asyncio.run(main())