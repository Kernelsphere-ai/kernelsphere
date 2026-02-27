import asyncio
import aiohttp
import random
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Information about a Browserbase session"""
    id: str
    connect_url: str
    created_at: datetime
    expires_at: datetime
    tasks_completed: int = 0
    is_healthy: bool = True
    last_used: Optional[datetime] = None


class RateLimiter:
    """
    Adaptive rate limiter that backs off when limits are hit
    """
    
    def __init__(self):
        self.consecutive_limits = 0
        self.last_limit_time: Optional[datetime] = None
        self.base_delay = 2.0  # Base delay in seconds
        self.max_delay = 300.0  # Max 5 minutes
        self.backoff_multiplier = 2.0
    
    def record_rate_limit(self):
        """Record that we hit a rate limit"""
        self.consecutive_limits += 1
        self.last_limit_time = datetime.now()
        logger.warning(f"Rate limit hit (consecutive: {self.consecutive_limits})")
    
    def record_success(self):
        """Record successful request"""
        if self.consecutive_limits > 0:
            logger.info(f"Rate limit cleared after {self.consecutive_limits} hits")
        self.consecutive_limits = 0
        self.last_limit_time = None
    
    def get_delay(self) -> float:
        """Get current delay with exponential backoff"""
        if self.consecutive_limits == 0:
            return 0.0
        
        # Exponential backoff: base * (multiplier ^ consecutive_limits)
        delay = self.base_delay * (self.backoff_multiplier ** (self.consecutive_limits - 1))
        
        jitter = delay * random.uniform(-0.2, 0.2)
        delay += jitter
        
        delay = min(delay, self.max_delay)
        
        return delay
    
    async def wait_if_needed(self):
        """Wait if we're being rate limited"""
        delay = self.get_delay()
        if delay > 0:
            logger.info(f"Rate limit backoff: waiting {delay:.1f}s")
            await asyncio.sleep(delay)


class ImprovedBrowserbaseSessionManager:
    """
    session manager with:
    1. Exponential backoff for rate limits
    2. Session pooling and reuse
    3. Circuit breaker for quota exhaustion
    4. Dynamic concurrency adjustment
    """
    
    def __init__(
        self,
        api_key: str,
        project_id: str,
        max_concurrent: int = 20,
        enable_pooling: bool = True,
        pool_size: int = 10,
        session_timeout: int = 600
    ):
        self.api_key = api_key
        self.project_id = project_id
        self.max_concurrent = max_concurrent
        self.enable_pooling = enable_pooling
        self.pool_size = pool_size
        self.session_timeout = session_timeout
        
        # Session pool
        self.session_pool: List[SessionInfo] = []
        self.active_sessions: Dict[str, SessionInfo] = {}
        
        # Rate limiting
        self.rate_limiter = RateLimiter()
        self.current_concurrency = max_concurrent
        
        # Circuit breaker
        self.circuit_open = False
        self.circuit_open_until: Optional[datetime] = None
        
        # Locks
        self.session_lock = asyncio.Lock()
        self.pool_lock = asyncio.Lock()
    
    async def get_session(self, task_id: str) -> Dict[str, Any]:
        """
        Get a session (reuse from pool or create new)
        """
        # Check circuit breaker
        if self._is_circuit_open():
            raise Exception("Circuit breaker open - quota likely exhausted")
        
        # Try to get from pool first
        if self.enable_pooling:
            pooled_session = await self._get_from_pool()
            if pooled_session:
                logger.info(f"Reusing session from pool: {pooled_session.id}")
                self.active_sessions[task_id] = pooled_session
                pooled_session.last_used = datetime.now()
                return {
                    'id': pooled_session.id,
                    'connectUrl': pooled_session.connect_url
                }
        
        # Create new session
        session_data = await self._create_session_with_retry()
        
        # Store session info
        session_info = SessionInfo(
            id=session_data['id'],
            connect_url=session_data['connectUrl'],
            created_at=datetime.now(),
            expires_at=datetime.fromisoformat(session_data['expiresAt'].replace('Z', '+00:00')),
            last_used=datetime.now()
        )
        
        self.active_sessions[task_id] = session_info
        
        return session_data
    
    async def _create_session_with_retry(self) -> Dict[str, Any]:
        """
        Create session with exponential backoff retry
        """
        max_attempts = 10  # Increased from 5
        
        for attempt in range(max_attempts):
            try:
                # Wait if rate limited
                await self.rate_limiter.wait_if_needed()
                
                # Wait for concurrency slot
                async with self.session_lock:
                    while len(self.active_sessions) >= self.current_concurrency:
                        logger.debug(f"Waiting for concurrency slot ({len(self.active_sessions)}/{self.current_concurrency})")
                        await asyncio.sleep(1)
                
                # Create session
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "x-bb-api-key": self.api_key,
                        "Content-Type": "application/json"
                    }
                    
                    payload = {
                        "projectId": self.project_id,
                        "browserSettings": {
                            "fingerprint": {
                                "browsers": ["chrome"],
                                "operatingSystems": ["windows"]
                            }
                        },
                        "timeout": self.session_timeout
                    }
                    
                    async with session.post(
                        "https://www.browserbase.com/v1/sessions",
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        
                        if response.status == 429:
                            # Rate limited
                            self.rate_limiter.record_rate_limit()
                            
                            # Reduce concurrency temporarily
                            await self._reduce_concurrency()
                            
                            # Get retry-after header if available
                            retry_after = response.headers.get('Retry-After')
                            if retry_after:
                                try:
                                    retry_delay = int(retry_after)
                                    logger.warning(f"Rate limited, server says wait {retry_delay}s")
                                    await asyncio.sleep(retry_delay)
                                except ValueError:
                                    pass
                            
                            continue
                        
                        if response.status == 403:
                            # Quota exhausted
                            logger.error("Quota exhausted (403)")
                            self._open_circuit_breaker(duration=300)  # 5 min
                            raise Exception("Browserbase quota exhausted")
                        
                        if response.status not in [200, 201]:
                            error_text = await response.text()
                            logger.error(f"Session creation failed: {response.status} - {error_text}")
                            
                            # Exponential backoff for errors
                            await asyncio.sleep(2 ** attempt)
                            continue
                        
                        # Success!
                        data = await response.json()
                        self.rate_limiter.record_success()
                        
                        # Gradually restore concurrency
                        await self._restore_concurrency()
                        
                        logger.info(
                            f"Created session {data['id']} "
                            f"(attempt {attempt + 1}, active: {len(self.active_sessions)})"
                        )
                        
                        return data
                
            except asyncio.TimeoutError:
                logger.warning(f"Session creation timeout (attempt {attempt + 1})")
                await asyncio.sleep(2 ** attempt)
                continue
                
            except Exception as e:
                logger.error(f"Session creation error (attempt {attempt + 1}): {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                else:
                    raise
        
        raise Exception(f"Failed to create session after {max_attempts} attempts")
    
    async def return_session(self, task_id: str, success: bool = True):
        """
        Return session to pool or close it
        """
        if task_id not in self.active_sessions:
            return
        
        session_info = self.active_sessions.pop(task_id)
        session_info.tasks_completed += 1
        
        # Check if session is still usable
        if self.enable_pooling and success and self._is_session_valid(session_info):
            # Return to pool
            async with self.pool_lock:
                if len(self.session_pool) < self.pool_size:
                    self.session_pool.append(session_info)
                    logger.info(f"Returned session {session_info.id} to pool (size: {len(self.session_pool)})")
                else:
                    # Pool full, close session
                    await self._close_session(session_info.id)
        else:
            # Close session
            await self._close_session(session_info.id)
    
    async def _get_from_pool(self) -> Optional[SessionInfo]:
        """Get session from pool"""
        async with self.pool_lock:
            # Remove expired sessions
            self.session_pool = [
                s for s in self.session_pool 
                if self._is_session_valid(s)
            ]
            
            if self.session_pool:
                return self.session_pool.pop(0)
            
            return None
    
    def _is_session_valid(self, session: SessionInfo) -> bool:
        """Check if session is still valid"""
        now = datetime.now()
        
        # Check expiration
        # Account for timezone awareness
        try:
            expires = session.expires_at.replace(tzinfo=None) if session.expires_at.tzinfo else session.expires_at
            if expires <= now:
                return False
        except:
            # If there's any issue with time comparison, consider invalid
            return False
        
        # Check if too many tasks
        if session.tasks_completed >= 5:  # Max 5 tasks per session
            return False
        
        return session.is_healthy
    
    async def _close_session(self, session_id: str):
        """Close a session"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"x-bb-api-key": self.api_key}
                async with session.post(
                    f"https://www.browserbase.com/v1/sessions/{session_id}/stop",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"Closed session {session_id}")
                    else:
                        logger.warning(f"Failed to close session {session_id}: {response.status}")
        except Exception as e:
            logger.error(f"Error closing session {session_id}: {e}")
    
    async def _reduce_concurrency(self):
        """Temporarily reduce concurrency when rate limited"""
        async with self.session_lock:
            old_concurrency = self.current_concurrency
            # Reduce by 50%, min 5
            self.current_concurrency = max(5, int(self.current_concurrency * 0.5))
            
            if old_concurrency != self.current_concurrency:
                logger.warning(
                    f"Reduced concurrency: {old_concurrency} → {self.current_concurrency}"
                )
    
    async def _restore_concurrency(self):
        """Gradually restore concurrency after successful requests"""
        async with self.session_lock:
            if self.current_concurrency < self.max_concurrent:
                old_concurrency = self.current_concurrency
                # Increase by 20%, max original
                self.current_concurrency = min(
                    self.max_concurrent,
                    int(self.current_concurrency * 1.2)
                )
                
                if old_concurrency != self.current_concurrency:
                    logger.info(
                        f"Restored concurrency: {old_concurrency} → {self.current_concurrency}"
                    )
    
    def _open_circuit_breaker(self, duration: int = 300):
        """Open circuit breaker (stop trying)"""
        self.circuit_open = True
        self.circuit_open_until = datetime.now() + timedelta(seconds=duration)
        logger.error(f"Circuit breaker opened for {duration}s")
    
    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open"""
        if not self.circuit_open:
            return False
        
        if self.circuit_open_until and datetime.now() >= self.circuit_open_until:
            # Circuit breaker timeout expired
            self.circuit_open = False
            self.circuit_open_until = None
            logger.info("Circuit breaker closed - retrying")
            return False
        
        return True
    
    async def cleanup_all(self):
        """Clean up all sessions"""
        logger.info("Cleaning up all sessions")
        
        # Close active sessions
        for session_info in list(self.active_sessions.values()):
            await self._close_session(session_info.id)
        
        # Close pooled sessions
        async with self.pool_lock:
            for session_info in self.session_pool:
                await self._close_session(session_info.id)
            self.session_pool.clear()
        
        self.active_sessions.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get session manager statistics"""
        return {
            "active_sessions": len(self.active_sessions),
            "pooled_sessions": len(self.session_pool),
            "current_concurrency": self.current_concurrency,
            "max_concurrency": self.max_concurrent,
            "rate_limit_hits": self.rate_limiter.consecutive_limits,
            "circuit_open": self.circuit_open,
            "pooling_enabled": self.enable_pooling
        }
