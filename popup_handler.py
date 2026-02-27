import asyncio
from playwright.async_api import Page
import logging


class PopupHandler:
    
    POPUP_PATTERNS = [
        {
            "selectors": [
                'button:has-text("Continue shopping")',
                'button:has-text("Continue Shopping")',
                'a:has-text("Continue shopping")'
            ],
            "name": "amazon_continue"
        },
        {
            "selectors": [
                'button:has-text("Accept")',
                'button:has-text("Accept all")',
                'button:has-text("Accept All Cookies")',
                '[id*="accept" i]:visible',
                '[class*="accept" i]:visible'
            ],
            "name": "cookie_consent"
        },
        {
            "selectors": [
                'button:has-text("Dismiss")',
                'button:has-text("Close")',
                'button[aria-label*="Close" i]',
                'button[aria-label*="Dismiss" i]',
                '[class*="close" i][role="button"]'
            ],
            "name": "generic_close"
        },
        {
            "selectors": [
                'button:has-text("No thanks")',
                'button:has-text("Not now")',
                'button:has-text("Maybe later")',
                'button:has-text("Skip")'
            ],
            "name": "promotional_decline"
        },
        {
            "selectors": [
                '[class*="modal" i] button:has-text("×")',
                '[class*="dialog" i] button:has-text("×")',
                '[class*="overlay" i] button:has-text("×")'
            ],
            "name": "modal_close"
        },
        {
            "selectors": [
                'button:has-text("Got it")',
                'button:has-text("OK")',
                'button:has-text("Okay")',
                'button:has-text("Understand")'
            ],
            "name": "acknowledgment"
        }
    ]
    
    def __init__(self, page: Page):
        self.page = page
        self.logger = logging.getLogger(__name__)
        self.handled_popups = set()
    
    async def check_and_handle_popups(self) -> bool:
        handled = False
        
        for pattern in self.POPUP_PATTERNS:
            for selector in pattern["selectors"]:
                try:
                    element = await self.page.query_selector(selector)
                    
                    if element and await element.is_visible():
                        popup_id = f"{pattern['name']}_{selector}"
                        
                        if popup_id not in self.handled_popups:
                            try:
                                await element.click(timeout=3000)
                                await asyncio.sleep(1)
                                self.handled_popups.add(popup_id)
                                self.logger.info(f"Handled popup: {pattern['name']}")
                                handled = True
                                return handled
                            except:
                                try:
                                    await element.click(force=True)
                                    await asyncio.sleep(1)
                                    self.handled_popups.add(popup_id)
                                    handled = True
                                    return handled
                                except:
                                    continue
                except:
                    continue
        
        try:
            overlays = await self.page.query_selector_all('[class*="overlay" i], [class*="modal" i], [class*="dialog" i]')
            for overlay in overlays:
                if await overlay.is_visible():
                    try:
                        await self.page.keyboard.press('Escape')
                        await asyncio.sleep(0.5)
                        self.logger.info("Dismissed overlay with Escape key")
                        handled = True
                        break
                    except:
                        pass
        except:
            pass
        
        return handled
    
    async def wait_for_no_popups(self, max_attempts: int = 3) -> None:
        for _ in range(max_attempts):
            handled = await self.check_and_handle_popups()
            if not handled:
                break
            await asyncio.sleep(0.5)
    
    def reset_handled_popups(self):
        self.handled_popups.clear()