import logging
from typing import Optional, Dict, Set
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)


@dataclass
class LoginState:
    """Tracks login state across agent execution"""
    is_logged_in: bool = False
    login_completed_at_step: Optional[int] = None
    user_email: Optional[str] = None
    login_indicators_found: Set[str] = None
    
    def __post_init__(self):
        if self.login_indicators_found is None:
            self.login_indicators_found = set()


class LoginStateDetector:
    """
    Detects and tracks login state to prevent infinite login loops
    """
    
    # Common indicators that user is logged in
    LOGGED_IN_INDICATORS = [
        # UI elements
        "log out", "logout", "sign out", "signout",
        "my account", "account settings", "profile settings",
        "welcome back", "welcome,",
        "dashboard", "my profile",
        
        # Actions
        "saved items", "my favorites", "my orders",
        "order history", "purchase history",
        
        # User info
        "signed in as", "logged in as",
    ]
    
    # Indicators that we're on a login page
    LOGIN_PAGE_INDICATORS = [
        "log in", "login", "sign in", "signin",
        "enter password", "enter your password",
        "email address", "username",
        "forgot password", "create account",
        "don't have an account",
        "verification code", "otp",
    ]
    
    def __init__(self):
        self.state = LoginState()
        self.login_attempts_count = 0
        self.max_login_attempts = 2  # Allow max 2 login attempts
        
    def detect_login_state(
        self, 
        page_text: str, 
        url: str,
        elements: list,
        step_num: int
    ) -> bool:
        """
        Detect if user is currently logged in
        
        Returns:
            True if logged in, False otherwise
        """
        if not page_text:
            return False
            
        page_text_lower = page_text.lower()
        url_lower = url.lower()
        
        # Count indicators
        logged_in_score = 0
        login_page_score = 0
        
        # Check text indicators
        for indicator in self.LOGGED_IN_INDICATORS:
            if indicator in page_text_lower:
                logged_in_score += 1
                self.state.login_indicators_found.add(indicator)
                
        for indicator in self.LOGIN_PAGE_INDICATORS:
            if indicator in page_text_lower:
                login_page_score += 1
        
        # Check URL patterns
        if any(pattern in url_lower for pattern in ['dashboard', 'account', 'profile', 'user']):
            logged_in_score += 2
            
        if any(pattern in url_lower for pattern in ['login', 'signin', 'auth', 'authenticate']):
            login_page_score += 2
        
        # Check for user email/name in elements
        if elements:
            for elem in elements[:50]:  # Check first 50 elements
                elem_text = str(elem).lower()
                if '@' in elem_text and '.com' in elem_text:
                    # Likely showing user's email
                    logged_in_score += 3
                    break
        
        # Decision logic
        is_logged_in = logged_in_score >= 2 and logged_in_score > login_page_score
        
        if is_logged_in and not self.state.is_logged_in:
            logger.info("="*60)
            logger.info(" LOGIN DETECTED!")
            logger.info(f" Indicators found: {self.state.login_indicators_found}")
            logger.info(f" Login score: {logged_in_score}, Login page score: {login_page_score}")
            logger.info("="*60)
            
            self.state.is_logged_in = True
            self.state.login_completed_at_step = step_num
            
        return is_logged_in
    
    def should_prevent_login_action(
        self,
        action: str,
        reasoning: str,
        step_num: int
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a login-related action should be blocked
        
        Returns:
            (should_block, reason)
        """
        # Actions that indicate login attempt
        login_actions = ['click', 'input_text', 'navigate']
        
        if action not in login_actions:
            return False, None
            
        reasoning_lower = reasoning.lower()
        
        # Check if reasoning mentions login
        login_keywords = [
            'log in', 'login', 'sign in', 'signin',
            'enter email', 'enter password',
            'authentication', 'authenticate'
        ]
        
        is_login_attempt = any(keyword in reasoning_lower for keyword in login_keywords)
        
        if not is_login_attempt:
            return False, None
            
        # We detected a login attempt
        self.login_attempts_count += 1
        
        # Block if we've already logged in
        if self.state.is_logged_in:
            reason = f"Login already completed at step {self.state.login_completed_at_step}. User is currently logged in."
            logger.warning("="*60)
            logger.warning(" BLOCKING REDUNDANT LOGIN ATTEMPT")
            logger.warning(f" Reason: {reason}")
            logger.warning(f" Action: {action}")
            logger.warning(f" Reasoning: {reasoning[:100]}...")
            logger.warning("="*60)
            return True, reason
            
        # Block if too many attempts
        if self.login_attempts_count > self.max_login_attempts:
            reason = f"Too many login attempts ({self.login_attempts_count}). Max allowed: {self.max_login_attempts}"
            logger.warning("="*60)
            logger.warning(" BLOCKING EXCESSIVE LOGIN ATTEMPTS")
            logger.warning(f" Reason: {reason}")
            logger.warning("="*60)
            return True, reason
            
        return False, None
    
    def mark_otp_completed(self, step_num: int):
        """
        Mark that OTP verification was completed successfully
        This is a strong signal that login succeeded
        """
        logger.info(f"OTP completed at step {step_num} - marking as logged in")
        self.state.is_logged_in = True
        self.state.login_completed_at_step = step_num
    
    def get_state(self) -> LoginState:
        """Get current login state"""
        return self.state
    
    def reset(self):
        """Reset state (for new tasks)"""
        self.state = LoginState()
        self.login_attempts_count = 0