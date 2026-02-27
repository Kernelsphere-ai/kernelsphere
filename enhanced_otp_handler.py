import asyncio
import logging

logger = logging.getLogger(__name__)

async def enhanced_input_otp(page, element, otp_code, dom_service):
    try:
        logger.info(f"OTP code: {otp_code}")
        
        initial_url = page.url
        
        individual_boxes_count = await page.locator('input[type="text"], input[type="tel"], input[type="number"], input[inputmode="numeric"]').count()
        
        if individual_boxes_count >= 4:
            logger.info(f"Detected {individual_boxes_count} individual OTP input boxes")
            
            boxes = page.locator('input[type="text"], input[type="tel"], input[type="number"], input[inputmode="numeric"]')
            
            for i, digit in enumerate(otp_code[:min(len(otp_code), individual_boxes_count)]):
                try:
                    box = boxes.nth(i)
                    await box.wait_for(state="visible", timeout=3000)
                    await box.click()
                    await asyncio.sleep(0.15)
                    
                    await box.fill('')
                    await asyncio.sleep(0.1)
                    
                    await box.fill(digit)
                    await asyncio.sleep(0.15)
                    
                    value = await box.input_value()
                    if value != digit:
                        await box.press_sequentially(digit, delay=50)
                        await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Failed to fill box {i}: {e}")
                    return False, f"Failed to fill box {i}: {e}"
            
            logger.info(f"Filled {min(len(otp_code), individual_boxes_count)} OTP boxes")
            
            await asyncio.sleep(2)
            
            if page.url != initial_url and 'auth' not in page.url.lower():
                logger.info("Auto-navigation detected after filling boxes")
                return True, "Auto-submitted"
            
            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Log')",
                "button:has-text('Submit')",
                "button:has-text('Verify')",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button:has-text('Confirm')"
            ]
            
            for selector in submit_selectors:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=1000):
                        await button.click()
                        logger.info(f"Clicked submit: {selector}")
                        await asyncio.sleep(3)
                        
                        if page.url != initial_url:
                            return True, "Submitted via button"
                except:
                    continue
            
            try:
                await page.keyboard.press("Enter")
                await asyncio.sleep(2)
                if page.url != initial_url:
                    logger.info("Enter key triggered submission")
                    return True, "Submitted via Enter"
            except:
                pass
            
            await asyncio.sleep(1)
            if page.url != initial_url and 'auth' not in page.url.lower():
                return True, "Navigation detected"
            
            return True, "OTP entered in boxes"
        
        single_input_selectors = [
            'input[name="totp"]',
            'input[type="text"][name*="code" i]',
            'input[type="text"][placeholder*="code" i]',
            'input[autocomplete="one-time-code"]',
            'input[inputmode="numeric"]',
            'input.otp-input',
            'input#totp',
            'input[type="tel"]',
            'input[type="text"]'
        ]
        
        for selector in single_input_selectors:
            try:
                locator = page.locator(selector).first
                await locator.wait_for(state="visible", timeout=2000)
                
                await locator.click()
                await asyncio.sleep(0.2)
                
                await locator.fill('')
                await asyncio.sleep(0.1)
                
                await locator.fill(otp_code)
                await asyncio.sleep(0.5)
                
                value = await locator.input_value()
                if value == otp_code:
                    logger.info(f"OTP verified: {otp_code}")
                else:
                    logger.warning(f"OTP mismatch: expected {otp_code}, got {value}")
                    await locator.fill(otp_code)
                    await asyncio.sleep(0.3)
                    
                    value = await locator.input_value()
                    if value != otp_code:
                        await locator.press_sequentially(otp_code, delay=80)
                        await asyncio.sleep(0.3)
                
                await asyncio.sleep(1)
                
                if page.url != initial_url:
                    logger.info("Auto-submit detected")
                    return True, "Auto-submitted"
                
                submit_selectors = [
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('Submit')",
                    "button:has-text('Verify')",
                    "button:has-text('Continue')",
                    "button:has-text('Next')",
                    "button:has-text('Log in')",
                    "button:has-text('Sign in')"
                ]
                
                for submit_sel in submit_selectors:
                    try:
                        button = page.locator(submit_sel).first
                        if await button.is_visible(timeout=1000):
                            await button.click()
                            logger.info(f"Clicked submit: {submit_sel}")
                            await asyncio.sleep(2)
                            return True, "Submitted via button"
                    except:
                        continue
                
                try:
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(1)
                    if page.url != initial_url:
                        logger.info("Enter key triggered submission")
                        return True, "Submitted via Enter"
                except:
                    pass
                
                return True, "OTP entered"
                
            except:
                continue
        
        return False, "OTP field not found"
        
    except Exception as e:
        logger.error(f"OTP error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False, str(e)

async def wait_for_navigation_after_otp(page, initial_url, timeout=10):
    logger.info(f"Waiting for navigation from: {initial_url}")
    
    start_time = asyncio.get_event_loop().time()
    
    while asyncio.get_event_loop().time() - start_time < timeout:
        current_url = page.url
        
        if current_url != initial_url:
            logger.info(f"Navigation detected to: {current_url}")
            return True
        
        await asyncio.sleep(0.5)
    
    logger.warning(f"No navigation after {timeout}s")
    return False