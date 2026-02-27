import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
import re
import time
import logging
from typing import Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class EmailOTPHandler:
    
    def __init__(self, email_address: str, email_password: str, imap_server: str = "imap.gmail.com"):
        self.email_address = email_address
        self.email_password = email_password
        self.imap_server = imap_server
        self.logger = logging.getLogger(__name__)
        self.last_sender = None
        self.request_timestamp = None
    
    def connect(self) -> Optional[imaplib.IMAP4_SSL]:
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_address, self.email_password)
            return mail
        except Exception as e:
            self.logger.error(f"Failed to connect to email: {e}")
            return None
    
    def get_latest_otp(self, sender_email: Optional[str] = None, timeout: int = 60) -> Optional[str]:
        self.request_timestamp = datetime.now(timezone.utc)
        start_time = time.time()
        
        self.logger.info(f"Searching for OTP code (timeout: {timeout}s)")
        self.logger.info(f"Request timestamp: {self.request_timestamp}")
        if sender_email:
            self.logger.info(f"Looking for emails from: {sender_email}")
        
        last_message_ids_seen = set()
        
        mail = self.connect()
        if mail:
            try:
                mail.select('inbox')
                search_criteria = f'(FROM "{sender_email}")' if sender_email else 'ALL'
                _, message_numbers = mail.search(None, search_criteria)
                if message_numbers[0]:
                    last_message_ids_seen = set(message_numbers[0].split())
                mail.close()
                mail.logout()
                self.logger.info(f"Baseline: {len(last_message_ids_seen)} existing messages")
            except:
                pass
        
        time.sleep(3)
        
        attempt_count = 0
        
        while time.time() - start_time < timeout:
            try:
                mail = self.connect()
                if not mail:
                    time.sleep(2)
                    continue
                
                mail.select('inbox')
                
                if sender_email:
                    search_criteria = f'(FROM "{sender_email}")'
                else:
                    search_criteria = 'ALL'
                
                _, message_numbers = mail.search(None, search_criteria)
                
                if message_numbers[0]:
                    current_message_ids = set(message_numbers[0].split())
                    new_message_ids = current_message_ids - last_message_ids_seen
                    
                    if new_message_ids:
                        self.logger.info(f"Found {len(new_message_ids)} new message(s)")
                        
                        new_ids_list = sorted(list(new_message_ids), reverse=True)
                        
                        for num in new_ids_list:
                            _, msg_data = mail.fetch(num, '(RFC822)')
                            
                            for response_part in msg_data:
                                if isinstance(response_part, tuple):
                                    email_message = email.message_from_bytes(response_part[1])
                                    
                                    from_header = email_message.get('From', '')
                                    from_email = parseaddr(from_header)[1].lower()
                                    
                                    if sender_email and sender_email.lower() not in from_email:
                                        continue
                                    
                                    date_header = email_message.get('Date', '')
                                    try:
                                        email_timestamp = parsedate_to_datetime(date_header)
                                        if email_timestamp.tzinfo is None:
                                            email_timestamp = email_timestamp.replace(tzinfo=timezone.utc)
                                        
                                        time_diff = (email_timestamp - self.request_timestamp).total_seconds()
                                        
                                        if time_diff < -30:
                                            self.logger.info(f"Skipping old email from {email_timestamp}")
                                            continue
                                    except:
                                        pass
                                    
                                    otp_code = self._extract_otp_from_message(email_message)
                                    
                                    if otp_code:
                                        self.logger.info(f"OTP code found: {otp_code} from {from_email}")
                                        
                                        try:
                                            mail.close()
                                        except:
                                            pass
                                        try:
                                            mail.logout()
                                        except:
                                            pass
                                        
                                        return otp_code
                        
                        last_message_ids_seen = current_message_ids
                
                try:
                    mail.close()
                except:
                    pass
                try:
                    mail.logout()
                except:
                    pass
                
                elapsed = int(time.time() - start_time)
                remaining = timeout - elapsed
                
                if remaining > 0:
                    attempt_count += 1
                    
                    if remaining > 5:
                        self.logger.info(f"No new OTP found yet, waiting... ({remaining}s remaining)")
                        time.sleep(3)
                    else:
                        time.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Error reading email: {e}")
                time.sleep(2)
        
        self.logger.error("OTP not found within timeout period")
        return None
    
    def _extract_otp_from_message(self, email_message) -> Optional[str]:
        try:
            body = ""
            subject = email_message.get('Subject', '')
            
            if email_message.is_multipart():
                for part in email_message.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            try:
                                body += payload.decode('utf-8', errors='ignore')
                            except:
                                try:
                                    body += payload.decode('latin-1', errors='ignore')
                                except:
                                    pass
                    elif content_type == "text/html" and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            try:
                                text = payload.decode('utf-8', errors='ignore')
                            except:
                                try:
                                    text = payload.decode('latin-1', errors='ignore')
                                except:
                                    text = ""
                            
                            import html
                            text = html.unescape(text)
                            text = re.sub('<[^<]+?>', ' ', text)
                            text = re.sub(r'\s+', ' ', text)
                            body += text
            else:
                payload = email_message.get_payload(decode=True)
                if payload:
                    try:
                        body = payload.decode('utf-8', errors='ignore')
                    except:
                        try:
                            body = payload.decode('latin-1', errors='ignore')
                        except:
                            pass
            
            search_text = subject + " " + body
            
            patterns = [
                (r'code[:\s]+(\d{6})', 'code-6'),
                (r'code[:\s]+(\d{5})', 'code-5'),
                (r'(?:verification|one-time|security)\s+code[:\s]+(\d{6})', 'context-6'),
                (r'(?:verification|one-time|security)\s+code[:\s]+(\d{5})', 'context-5'),
                (r'(?:your|the|enter)\s+code[:\s]+(\d{6})', 'your-code-6'),
                (r'(?:your|the|enter)\s+code[:\s]+(\d{5})', 'your-code-5'),
                (r'code\s+is[:\s]+(\d{6})', 'is-6'),
                (r'code\s+is[:\s]+(\d{5})', 'is-5'),
                (r'\b(\d{6})\s+(?:is your|to verify|to log|to sign)', 'post-6'),
                (r'\b(\d{5})\s+(?:is your|to verify|to log|to sign)', 'post-5'),
                (r'(?:use|enter)\s+(\d{6})', 'use-6'),
                (r'(?:use|enter)\s+(\d{5})', 'use-5'),
            ]
            
            for pattern, name in patterns:
                matches = list(re.finditer(pattern, search_text, re.IGNORECASE))
                for match in matches:
                    code = match.group(1)
                    if self._is_valid_otp(code):
                        self.logger.info(f"Extracted using pattern: {name}")
                        return code
            
            all_codes = re.findall(r'\b(\d{4,6})\b', body)
            
            valid_codes = [c for c in all_codes if self._is_valid_otp(c)]
            
            if len(valid_codes) == 1:
                self.logger.info(f"Single valid code found: {valid_codes[0]}")
                return valid_codes[0]
            
            if len(valid_codes) > 1:
                for code in valid_codes:
                    if len(code) == 5 or len(code) == 6:
                        self.logger.info(f"First valid code: {code}")
                        return code
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error extracting OTP: {e}")
            return None
    
    def _is_valid_otp(self, code: str) -> bool:
        if not code:
            return False
        
        if len(code) not in [4, 5, 6]:
            return False
        
        if not code.isdigit():
            return False
        
        code_int = int(code)
        if code_int == 0:
            return False
        
        if code in ['00000', '11111', '22222', '33333', '44444', '55555', '66666', '77777', '88888', '99999']:
            return False
        
        if code in ['123456', '000000', '111111', '654321', '12345']:
            return False
        
        if len(code) >= 5:
            current_year = datetime.now().year
            if str(current_year) in code or str(current_year-1) in code:
                return False
        
        return True
    
    def clear_old_messages(self, sender_email: Optional[str] = None):
        try:
            mail = self.connect()
            if not mail:
                return
            
            mail.select('inbox')
            
            if sender_email:
                search_criteria = f'(FROM "{sender_email}" SEEN)'
            else:
                search_criteria = 'SEEN'
            
            _, message_numbers = mail.search(None, search_criteria)
            
            if message_numbers[0]:
                for num in message_numbers[0].split():
                    mail.store(num, '+FLAGS', '\\Deleted')
            
            mail.expunge()
            
            try:
                mail.close()
            except:
                pass
            try:
                mail.logout()
            except:
                pass
            
            self.logger.info("Cleared old messages")
            
        except Exception as e:
            self.logger.error(f"Error clearing old messages: {e}")