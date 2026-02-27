import os
import json
import asyncio
import logging
import random
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from enum import Enum
import aiohttp
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


class ProxyProvider(Enum):
    PROXYEMPIRE = "proxyempire"
    SMARTPROXY = "smartproxy"
    OXYLABS = "oxylabs"
    WEBSHARE = "webshare"
    PROXY6 = "proxy6"
    CUSTOM = "custom"


class ProxyType(Enum):
    RESIDENTIAL = "residential"
    DATACENTER = "datacenter"
    MOBILE = "mobile"


@dataclass
class ProxyConfig:
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    country: Optional[str] = None
    provider: ProxyProvider = ProxyProvider.CUSTOM
    proxy_type: ProxyType = ProxyType.RESIDENTIAL
    
    def to_url(self) -> str:
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"
    
    def to_playwright_dict(self) -> Dict[str, Any]:
        proxy_dict = {
            "server": f"{self.protocol}://{self.host}:{self.port}"
        }
        if self.username and self.password:
            proxy_dict["username"] = self.username
            proxy_dict["password"] = self.password
        return proxy_dict
    
    def to_browserbase_dict(self) -> Dict[str, Any]:
        config = {
            "type": self.protocol,
            "host": self.host,
            "port": self.port
        }
        if self.username:
            config["username"] = self.username
        if self.password:
            config["password"] = self.password
        return config


@dataclass
class ProxyHealth:
    proxy: ProxyConfig
    last_check: datetime
    success_count: int = 0
    failure_count: int = 0
    avg_response_time: float = 0.0
    is_healthy: bool = True
    last_error: Optional[str] = None
    
    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return (self.success_count / total * 100) if total > 0 else 0.0


class ProxyPool:
    
    def __init__(self, check_interval: int = 300):
        self.proxies: List[ProxyHealth] = []
        self.check_interval = check_interval
        self.current_index = 0
        self._lock = asyncio.Lock()
        self.health_check_task: Optional[asyncio.Task] = None
    
    def add_proxy(self, proxy: ProxyConfig):
        health = ProxyHealth(
            proxy=proxy,
            last_check=datetime.now()
        )
        self.proxies.append(health)
        logger.info(f"Added proxy: {proxy.host}:{proxy.port} ({proxy.provider.value})")
    
    def add_proxies(self, proxies: List[ProxyConfig]):
        for proxy in proxies:
            self.add_proxy(proxy)
    
    async def get_next_proxy(self) -> Optional[ProxyConfig]:
        async with self._lock:
            if not self.proxies:
                return None
            
            healthy_proxies = [p for p in self.proxies if p.is_healthy]
            
            if not healthy_proxies:
                logger.warning("No healthy proxies available, using any proxy")
                healthy_proxies = self.proxies
            
            if not healthy_proxies:
                return None
            
            healthy_proxies.sort(key=lambda x: (x.success_rate, -x.avg_response_time), reverse=True)
            
            proxy_health = healthy_proxies[0]
            self.current_index = (self.current_index + 1) % len(healthy_proxies)
            
            return proxy_health.proxy
    
    async def get_random_proxy(self) -> Optional[ProxyConfig]:
        async with self._lock:
            if not self.proxies:
                return None
            
            healthy_proxies = [p for p in self.proxies if p.is_healthy]
            
            if not healthy_proxies:
                healthy_proxies = self.proxies
            
            if not healthy_proxies:
                return None
            
            return random.choice(healthy_proxies).proxy
    
    async def get_proxy_by_country(self, country: str) -> Optional[ProxyConfig]:
        async with self._lock:
            matching = [p for p in self.proxies if p.proxy.country == country and p.is_healthy]
            
            if not matching:
                matching = [p for p in self.proxies if p.proxy.country == country]
            
            if not matching:
                return None
            
            return random.choice(matching).proxy
    
    async def mark_proxy_success(self, proxy: ProxyConfig, response_time: float):
        async with self._lock:
            for p in self.proxies:
                if p.proxy.host == proxy.host and p.proxy.port == proxy.port:
                    p.success_count += 1
                    p.last_check = datetime.now()
                    p.is_healthy = True
                    p.last_error = None
                    
                    if p.avg_response_time == 0:
                        p.avg_response_time = response_time
                    else:
                        p.avg_response_time = (p.avg_response_time * 0.7 + response_time * 0.3)
                    
                    logger.debug(f"Proxy success: {proxy.host}:{proxy.port} ({response_time:.2f}s)")
                    break
    
    async def mark_proxy_failure(self, proxy: ProxyConfig, error: str):
        async with self._lock:
            for p in self.proxies:
                if p.proxy.host == proxy.host and p.proxy.port == proxy.port:
                    p.failure_count += 1
                    p.last_check = datetime.now()
                    p.last_error = error
                    
                    if p.failure_count >= 3:
                        p.is_healthy = False
                        logger.warning(f"Proxy marked unhealthy: {proxy.host}:{proxy.port} - {error}")
                    
                    break
    
    async def check_proxy_health(self, proxy_health: ProxyHealth) -> bool:
        test_urls = [
            "https://www.google.com",
            "https://httpbin.org/ip"
        ]
        
        proxy_url = proxy_health.proxy.to_url()
        
        try:
            async with aiohttp.ClientSession() as session:
                start_time = datetime.now()
                
                async with session.get(
                    random.choice(test_urls),
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=False
                ) as response:
                    if response.status == 200:
                        response_time = (datetime.now() - start_time).total_seconds()
                        await self.mark_proxy_success(proxy_health.proxy, response_time)
                        return True
                    else:
                        await self.mark_proxy_failure(proxy_health.proxy, f"HTTP {response.status}")
                        return False
                        
        except Exception as e:
            await self.mark_proxy_failure(proxy_health.proxy, str(e))
            return False
    
    async def health_check_loop(self):
        while True:
            try:
                logger.info("Starting proxy health check cycle")
                
                for proxy_health in self.proxies:
                    await self.check_proxy_health(proxy_health)
                    await asyncio.sleep(1)
                
                healthy_count = sum(1 for p in self.proxies if p.is_healthy)
                logger.info(f"Health check complete: {healthy_count}/{len(self.proxies)} proxies healthy")
                
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in health check loop: {e}")
                await asyncio.sleep(60)
    
    def start_health_checks(self):
        if self.health_check_task is None or self.health_check_task.done():
            self.health_check_task = asyncio.create_task(self.health_check_loop())
            logger.info("Started proxy health monitoring")
    
    def stop_health_checks(self):
        if self.health_check_task and not self.health_check_task.done():
            self.health_check_task.cancel()
            logger.info("Stopped proxy health monitoring")
    
    def get_stats(self) -> Dict:
        total = len(self.proxies)
        healthy = sum(1 for p in self.proxies if p.is_healthy)
        
        return {
            "total_proxies": total,
            "healthy_proxies": healthy,
            "unhealthy_proxies": total - healthy,
            "proxies": [
                {
                    "host": p.proxy.host,
                    "port": p.proxy.port,
                    "provider": p.proxy.provider.value,
                    "is_healthy": p.is_healthy,
                    "success_rate": p.success_rate,
                    "avg_response_time": p.avg_response_time,
                    "last_error": p.last_error
                }
                for p in self.proxies
            ]
        }


class ProxyManager:
    
    def __init__(self, config_file: Optional[str] = None):
        self.pool = ProxyPool()
        self.config_file = config_file or os.getenv('PROXY_CONFIG_FILE', 'proxy_config.json')
        self.session_proxies: Dict[str, ProxyConfig] = {}
        
        self._load_from_env()
        self._load_from_config()
    
    def _load_from_env(self):
        
        proxyempire_user = os.getenv('PROXYEMPIRE_USERNAME')
        proxyempire_pass = os.getenv('PROXYEMPIRE_PASSWORD')
        proxyempire_host = os.getenv('PROXYEMPIRE_HOST', 'residential.proxyempire.io')
        proxyempire_port = int(os.getenv('PROXYEMPIRE_PORT', '9000'))
        proxyempire_country = os.getenv('PROXYEMPIRE_COUNTRY', '')
        proxyempire_session = os.getenv('PROXYEMPIRE_SESSION', '')
        
        if proxyempire_user and proxyempire_pass:
            username = proxyempire_user
            
            if proxyempire_country:
                username = f"{proxyempire_user}-country-{proxyempire_country}"
            
            if proxyempire_session:
                username = f"{username}-session-{proxyempire_session}"
            
            proxy = ProxyConfig(
                host=proxyempire_host,
                port=proxyempire_port,
                username=username,
                password=proxyempire_pass,
                protocol="http",
                provider=ProxyProvider.PROXYEMPIRE,
                proxy_type=ProxyType.RESIDENTIAL
            )
            self.pool.add_proxy(proxy)
            logger.info("Loaded ProxyEmpire proxy from environment")
        
        smartproxy_user = os.getenv('SMARTPROXY_USERNAME')
        smartproxy_pass = os.getenv('SMARTPROXY_PASSWORD')
        smartproxy_host = os.getenv('SMARTPROXY_HOST', 'gate.smartproxy.com')
        smartproxy_port = int(os.getenv('SMARTPROXY_PORT', '7000'))
        
        if smartproxy_user and smartproxy_pass:
            proxy = ProxyConfig(
                host=smartproxy_host,
                port=smartproxy_port,
                username=smartproxy_user,
                password=smartproxy_pass,
                protocol="http",
                provider=ProxyProvider.SMARTPROXY,
                proxy_type=ProxyType.RESIDENTIAL
            )
            self.pool.add_proxy(proxy)
            logger.info("Loaded SmartProxy from environment")
        
        oxylabs_user = os.getenv('OXYLABS_USERNAME')
        oxylabs_pass = os.getenv('OXYLABS_PASSWORD')
        oxylabs_host = os.getenv('OXYLABS_HOST', 'pr.oxylabs.io')
        oxylabs_port = int(os.getenv('OXYLABS_PORT', '7777'))
        
        if oxylabs_user and oxylabs_pass:
            proxy = ProxyConfig(
                host=oxylabs_host,
                port=oxylabs_port,
                username=oxylabs_user,
                password=oxylabs_pass,
                protocol="http",
                provider=ProxyProvider.OXYLABS,
                proxy_type=ProxyType.RESIDENTIAL
            )
            self.pool.add_proxy(proxy)
            logger.info("Loaded Oxylabs proxy from environment")
        
        webshare_user = os.getenv('WEBSHARE_USERNAME')
        webshare_pass = os.getenv('WEBSHARE_PASSWORD')
        webshare_host = os.getenv('WEBSHARE_HOST', 'proxy.webshare.io')
        webshare_port = int(os.getenv('WEBSHARE_PORT', '80'))
        
        if webshare_user and webshare_pass:
            proxy = ProxyConfig(
                host=webshare_host,
                port=webshare_port,
                username=webshare_user,
                password=webshare_pass,
                protocol="http",
                provider=ProxyProvider.WEBSHARE,
                proxy_type=ProxyType.DATACENTER
            )
            self.pool.add_proxy(proxy)
            logger.info("Loaded Webshare proxy from environment")
    
    def _load_from_config(self):
        if not os.path.exists(self.config_file):
            logger.info(f"No proxy config file found at {self.config_file}")
            return
        
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            for proxy_data in config.get('proxies', []):
                try:
                    provider_str = proxy_data.get('provider', 'custom')
                    provider = ProxyProvider[provider_str.upper()]
                except KeyError:
                    provider = ProxyProvider.CUSTOM
                
                try:
                    proxy_type_str = proxy_data.get('type', 'residential')
                    proxy_type = ProxyType[proxy_type_str.upper()]
                except KeyError:
                    proxy_type = ProxyType.RESIDENTIAL
                
                proxy = ProxyConfig(
                    host=proxy_data['host'],
                    port=proxy_data['port'],
                    username=proxy_data.get('username'),
                    password=proxy_data.get('password'),
                    protocol=proxy_data.get('protocol', 'http'),
                    country=proxy_data.get('country'),
                    provider=provider,
                    proxy_type=proxy_type
                )
                self.pool.add_proxy(proxy)
            
            logger.info(f"Loaded {len(config.get('proxies', []))} proxies from config file")
            
        except Exception as e:
            logger.error(f"Failed to load proxy config: {e}")
    
    async def get_proxy_for_session(self, session_id: str, country: Optional[str] = None) -> Optional[ProxyConfig]:
        if session_id in self.session_proxies:
            return self.session_proxies[session_id]
        
        if country:
            proxy = await self.pool.get_proxy_by_country(country)
            if proxy:
                self.session_proxies[session_id] = proxy
                return proxy
        
        proxy = await self.pool.get_next_proxy()
        if proxy:
            self.session_proxies[session_id] = proxy
        
        return proxy
    
    async def get_random_proxy(self) -> Optional[ProxyConfig]:
        return await self.pool.get_random_proxy()
    
    async def mark_session_success(self, session_id: str, response_time: float = 1.0):
        if session_id in self.session_proxies:
            await self.pool.mark_proxy_success(self.session_proxies[session_id], response_time)
    
    async def mark_session_failure(self, session_id: str, error: str):
        if session_id in self.session_proxies:
            await self.pool.mark_proxy_failure(self.session_proxies[session_id], error)
    
    def release_session(self, session_id: str):
        if session_id in self.session_proxies:
            del self.session_proxies[session_id]
    
    def start_health_monitoring(self):
        self.pool.start_health_checks()
    
    def stop_health_monitoring(self):
        self.pool.stop_health_checks()
    
    def get_stats(self) -> Dict:
        stats = self.pool.get_stats()
        stats['active_sessions'] = len(self.session_proxies)
        return stats
    
    def has_proxies(self) -> bool:
        return len(self.pool.proxies) > 0


_global_proxy_manager: Optional[ProxyManager] = None


def get_proxy_manager() -> ProxyManager:
    global _global_proxy_manager
    if _global_proxy_manager is None:
        _global_proxy_manager = ProxyManager()
    return _global_proxy_manager


async def test_proxy_manager():
    manager = ProxyManager()
    
    if not manager.has_proxies():
        logger.warning("No proxies configured")
        return
    
    manager.start_health_monitoring()
    
    proxy = await manager.get_proxy_for_session("test-session")
    if proxy:
        logger.info(f"Got proxy: {proxy.host}:{proxy.port}")
    
    await asyncio.sleep(10)
    
    stats = manager.get_stats()
    logger.info(f"Proxy stats: {json.dumps(stats, indent=2)}")
    
    manager.stop_health_monitoring()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_proxy_manager())