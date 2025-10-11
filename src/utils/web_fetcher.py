"""
Web Fetcher Module for Invoice Processing
Handles downloading PDFs from web links in emails
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class WebFetcher:
    """Fetches and processes web-based invoice PDFs"""

    def __init__(self, timeout: int = 30):
        """
        Initialize web fetcher

        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

    def extract_urls_from_email(self, email_body: str, url_patterns: List[str] = None) -> List[str]:
        """
        Extract URLs from email body text

        Args:
            email_body: Email body content (plain text or HTML)
            url_patterns: Optional regex patterns to filter URLs

        Returns:
            List of extracted URLs
        """
        urls = []

        # Extract all URLs using regex
        url_regex = r'https?://[^\s<>"{}|\\^`\[\]]+'
        found_urls = re.findall(url_regex, email_body, re.IGNORECASE)

        logger.info(f"Found {len(found_urls)} URLs in email body")

        # Filter by patterns if provided
        if url_patterns:
            for url in found_urls:
                for pattern in url_patterns:
                    if re.search(pattern, url):
                        urls.append(url)
                        logger.info(f"Matched URL: {url}")
                        break
        else:
            urls = found_urls

        return urls

    def fetch_web_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Fetch web page content

        Args:
            url: URL to fetch

        Returns:
            Tuple of (html_content, text_content) or (None, None) on error
        """
        try:
            logger.info(f"Fetching web page: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            html_content = response.text

            # Parse HTML and extract text
            soup = BeautifulSoup(html_content, 'html.parser')

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()

            text_content = soup.get_text(separator='\n', strip=True)

            logger.info(f"Successfully fetched page ({len(html_content)} bytes)")
            return html_content, text_content

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch web page {url}: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Error processing web page {url}: {e}")
            return None, None

    def _extract_aspnet_viewstate(self, html_content: str) -> Dict[str, str]:
        """
        Extract ASP.NET ViewState and EventValidation from HTML form

        Args:
            html_content: HTML content containing ASP.NET form

        Returns:
            Dictionary with ViewState fields
        """
        viewstate_data = {}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Find ViewState hidden field
            viewstate = soup.find('input', {'id': '__VIEWSTATE'})
            if viewstate:
                viewstate_data['__VIEWSTATE'] = viewstate.get('value', '')
                logger.info(f"Extracted __VIEWSTATE ({len(viewstate_data['__VIEWSTATE'])} chars)")

            # Find ViewStateGenerator hidden field
            viewstate_gen = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
            if viewstate_gen:
                viewstate_data['__VIEWSTATEGENERATOR'] = viewstate_gen.get('value', '')
                logger.info(f"Extracted __VIEWSTATEGENERATOR: {viewstate_data['__VIEWSTATEGENERATOR']}")

            # Find EventValidation hidden field
            event_validation = soup.find('input', {'id': '__EVENTVALIDATION'})
            if event_validation:
                viewstate_data['__EVENTVALIDATION'] = event_validation.get('value', '')
                logger.info(f"Extracted __EVENTVALIDATION ({len(viewstate_data['__EVENTVALIDATION'])} chars)")

        except Exception as e:
            logger.error(f"Error extracting ViewState: {e}")

        return viewstate_data

    def _submit_aspnet_form_for_pdf(
        self,
        page_url: str,
        html_content: str,
        button_name: str
    ) -> Optional[bytes]:
        """
        Submit ASP.NET form to download PDF

        Args:
            page_url: URL of the page containing the form
            html_content: HTML content of the page
            button_name: Name attribute of the submit button (e.g., '_ctl0:content:btnPdf')

        Returns:
            PDF content as bytes or None on error
        """
        try:
            logger.info(f"Submitting ASP.NET form with button: {button_name}")

            # Extract ViewState data
            form_data = self._extract_aspnet_viewstate(html_content)

            if not form_data:
                logger.error("No ViewState data found in form")
                return None

            # Add button click event
            # ASP.NET expects the button name with .x and .y coordinates
            form_data[f'{button_name}.x'] = '0'
            form_data[f'{button_name}.y'] = '0'

            logger.info(f"Submitting form with {len(form_data)} fields")

            # Submit POST request
            response = self.session.post(
                page_url,
                data=form_data,
                timeout=self.timeout
            )
            response.raise_for_status()

            # Check if response is a PDF
            content_type = response.headers.get('Content-Type', '').lower()
            if 'pdf' in content_type or response.content.startswith(b'%PDF'):
                logger.info(f"Successfully downloaded PDF via ASP.NET form ({len(response.content)} bytes)")
                return response.content
            else:
                logger.warning(f"ASP.NET form response is not a PDF (Content-Type: {content_type})")
                return None

        except Exception as e:
            logger.error(f"Error submitting ASP.NET form: {e}")
            return None

    def extract_pdf_download_link(
        self,
        html_content: str,
        base_url: str,
        link_text_patterns: List[str] = None
    ) -> Optional[str]:
        """
        Extract PDF download link from HTML content

        Args:
            html_content: HTML content of the page
            base_url: Base URL for resolving relative links
            link_text_patterns: Text patterns to identify the download link

        Returns:
            PDF download URL or None (for ASP.NET forms, returns special marker)
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Find all links
            links = soup.find_all('a', href=True)

            logger.info(f"Found {len(links)} links on page")

            # Strategy 1: Look for links with matching text
            if link_text_patterns:
                for link in links:
                    link_text = link.get_text(strip=True)
                    for pattern in link_text_patterns:
                        if pattern.lower() in link_text.lower():
                            href = link['href']
                            pdf_url = urljoin(base_url, href)
                            logger.info(f"Found PDF link by text '{pattern}': {pdf_url}")
                            return pdf_url

            # Strategy 2: Look for links ending with .pdf
            for link in links:
                href = link['href']
                if href.lower().endswith('.pdf') or '.pdf?' in href.lower():
                    pdf_url = urljoin(base_url, href)
                    logger.info(f"Found PDF link by extension: {pdf_url}")
                    return pdf_url

            # Strategy 3: Look for links with 'pdf' in the href or onclick
            for link in links:
                href = link['href']
                onclick = link.get('onclick', '')
                if 'pdf' in href.lower() or 'pdf' in onclick.lower():
                    pdf_url = urljoin(base_url, href)
                    logger.info(f"Found PDF link by keyword: {pdf_url}")
                    return pdf_url

            # Strategy 4: Look for ASP.NET form buttons with PDF-related titles
            if link_text_patterns:
                for pattern in link_text_patterns:
                    # Find input buttons with title matching pattern
                    buttons = soup.find_all('input', {'type': 'image', 'title': re.compile(pattern, re.IGNORECASE)})
                    if buttons:
                        button = buttons[0]
                        button_name = button.get('name', '')
                        logger.info(f"Found ASP.NET PDF button by title '{pattern}': {button_name}")
                        # Return special marker for ASP.NET form
                        return f"ASPNET_FORM:{button_name}"

                    # Also check for regular submit buttons
                    buttons = soup.find_all('input', {'type': 'submit', 'value': re.compile(pattern, re.IGNORECASE)})
                    if buttons:
                        button = buttons[0]
                        button_name = button.get('name', '')
                        logger.info(f"Found ASP.NET PDF submit button by value '{pattern}': {button_name}")
                        return f"ASPNET_FORM:{button_name}"

            logger.warning("No PDF download link found on page")
            return None

        except Exception as e:
            logger.error(f"Error extracting PDF link: {e}")
            return None

    def download_pdf(self, url: str) -> Optional[bytes]:
        """
        Download PDF from URL

        Args:
            url: PDF download URL

        Returns:
            PDF content as bytes or None on error
        """
        try:
            logger.info(f"Downloading PDF from: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            # Verify content type
            content_type = response.headers.get('Content-Type', '').lower()
            if 'pdf' not in content_type and 'application/octet-stream' not in content_type:
                logger.warning(f"Unexpected content type: {content_type}")

            pdf_data = response.content
            logger.info(f"Downloaded PDF ({len(pdf_data)} bytes)")

            # Basic PDF validation
            if not pdf_data.startswith(b'%PDF'):
                logger.error("Downloaded content is not a valid PDF")
                return None

            return pdf_data

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download PDF from {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading PDF: {e}")
            return None

    def extract_data_from_web_page(
        self,
        text_content: str,
        extraction_patterns: Dict[str, str]
    ) -> Dict[str, Optional[str]]:
        """
        Extract invoice data from web page text

        Args:
            text_content: Plain text content of the page
            extraction_patterns: Dict of field_name -> regex_pattern

        Returns:
            Dict of field_name -> extracted_value
        """
        extracted_data = {}

        for field_name, pattern in extraction_patterns.items():
            try:
                match = re.search(pattern, text_content, re.IGNORECASE | re.MULTILINE)
                if match:
                    if match.groups():
                        extracted_data[field_name] = match.groups()
                    else:
                        extracted_data[field_name] = match.group(0)
                    logger.info(f"Extracted {field_name}: {extracted_data[field_name]}")
                else:
                    extracted_data[field_name] = None
                    logger.warning(f"Could not extract {field_name}")
            except Exception as e:
                logger.error(f"Error extracting {field_name}: {e}")
                extracted_data[field_name] = None

        return extracted_data

    def process_web_invoice(
        self,
        email_body: str,
        rule: Dict[str, Any]
    ) -> Tuple[Optional[bytes], Dict[str, Any], Optional[str]]:
        """
        Complete workflow for processing web-based invoices

        Args:
            email_body: Email body content
            rule: Invoice processing rule with web_extraction config

        Returns:
            Tuple of (pdf_data, extracted_data, web_page_text)
        """
        web_config = rule.get('web_extraction', {})

        # Step 1: Extract invoice page URL from email
        url_patterns = web_config.get('invoice_page_url_patterns', [])
        urls = self.extract_urls_from_email(email_body, url_patterns)

        if not urls:
            logger.error("No matching URLs found in email body")
            return None, {}, None

        invoice_url = urls[0]  # Use first matching URL
        logger.info(f"Using invoice URL: {invoice_url}")

        # Step 2: Fetch the invoice web page
        html_content, text_content = self.fetch_web_page(invoice_url)

        if not html_content:
            logger.error("Failed to fetch invoice web page")
            return None, {}, None

        # Step 3: Extract data from web page (optional but useful)
        web_data_patterns = web_config.get('web_page_data_extraction', {})
        extracted_data = self.extract_data_from_web_page(text_content, web_data_patterns)

        # Step 4: Extract PDF download link or ASP.NET form button
        link_patterns = web_config.get('pdf_download_link_patterns', [])
        pdf_url = self.extract_pdf_download_link(html_content, invoice_url, link_patterns)

        if not pdf_url:
            logger.error("Could not find PDF download link on page")
            return None, extracted_data, text_content

        # Step 5: Download the PDF
        # Check if this is an ASP.NET form
        if pdf_url.startswith('ASPNET_FORM:'):
            button_name = pdf_url.replace('ASPNET_FORM:', '')
            logger.info(f"Downloading PDF via ASP.NET form submission (button: {button_name})")
            pdf_data = self._submit_aspnet_form_for_pdf(invoice_url, html_content, button_name)
        else:
            # Regular PDF download
            pdf_data = self.download_pdf(pdf_url)

        if not pdf_data:
            logger.error("Failed to download PDF")
            return None, extracted_data, text_content

        return pdf_data, extracted_data, text_content

    def close(self):
        """Close the HTTP session"""
        self.session.close()


# Factory function
def create_web_fetcher(timeout: int = 30) -> WebFetcher:
    """Create and initialize web fetcher"""
    return WebFetcher(timeout=timeout)


# Example usage
if __name__ == "__main__":
    # Test with sample email body
    sample_email_body = """
    Számláját itt tekintheti meg és fizetheti be: Számla megtekintése és befizetése
    https://online.yettel.hu/ugyfelszolgalat/fwk/invoice.aspx?invoiceno=100338820861&Id=350993412
    """

    fetcher = create_web_fetcher()

    # Extract URLs
    urls = fetcher.extract_urls_from_email(
        sample_email_body,
        ["https://online\\.yettel\\.hu/ugyfelszolgalat/fwk/invoice\\.aspx\\?invoiceno=[0-9]+&Id=[0-9]+"]
    )

    print(f"Found URLs: {urls}")
