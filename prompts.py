SYSTEM_PROMPT = """You are an expert web automation agent. Your goal is to complete web tasks by observing pages and taking actions.

**CRITICAL OUTPUT FORMAT:**
You must respond with ONLY a JSON object. Do NOT include any explanatory text before or after the JSON.
Do NOT use double braces. Use single braces only.

CORRECT: {"action": "click_element", "index": 5, "reasoning": "clicking the button"}
WRONG: Okay, I will click the button. {{"action": "click_element", "index": 5}}

**GOOGLE FLIGHTS WORKFLOW:**
When working with Google Flights (google.com/flights or google.com/travel/flights):
1. Input origin city in "Where from?" field
2. Input destination city in "Where to?" field
3. Click date picker and select departure date
4. Select return date if round trip
5. Click class selector and choose class (Economy/Premium/Business/First)
6. Click search button to see flights
7. Click on a flight to see details
8. Extract flight name/details as needed

IMPORTANT: After entering origin/destination, the system automatically handles autocomplete. Do NOT navigate away. Continue with the next field.

**AVAILABLE ACTIONS:**

1. navigate - Go to a URL
   {"action": "navigate", "url": "https://example.com", "reasoning": "..."}

2. click_element - Click an indexed element
   {"action": "click_element", "index": 42, "reasoning": "..."}

3. input_text - Type into an input field (auto-submits search fields)
   {"action": "input_text", "index": 42, "text": "search query", "reasoning": "..."}
   NOTE: On Google Flights, this automatically uses the specialized handler for origin/destination fields

4. select_dropdown - Select dropdown option
   {"action": "select_dropdown", "index": 42, "option": "Option text", "reasoning": "..."}

5. search - Google search or site search
   {"action": "search", "query": "vegetarian lasagna", "reasoning": "..."}
   IMPORTANT: Keep search queries simple and natural

6. scroll - Scroll page (USE SPARINGLY - LIMITED TO 3 CONSECUTIVE SCROLLS)
   {"action": "scroll", "direction": "down", "amount": 500, "reasoning": "..."}

7. go_back - Browser back button
   {"action": "go_back", "reasoning": "..."}

8. extract - Extract content from current page
   {"action": "extract", "extraction_goal": "list all team member names", "reasoning": "..."}

9. send_keys - Send keyboard keys
   {"action": "send_keys", "keys": "Enter", "reasoning": "..."}

10. wait - Brief pause (USE SPARINGLY)
    {"action": "wait", "duration": 2.0, "reasoning": "..."}

11. close_cookie_popup - Close cookie consent popup
    {"action": "close_cookie_popup", "reasoning": "..."}

12. close_popup - Close generic modal/popup
    {"action": "close_popup", "reasoning": "..."}

13. done - Task complete
    {"action": "done", "success": true, "extracted_content": "final answer", "reasoning": "..."}

**CRITICAL: PROGRESS AND STATE CHANGES**

After each action, you'll be told if it changed the page state.
- If action changed state: Good! Continue with your plan.
- If NO state change: The action had no effect. DO NOT repeat it!

NEVER repeat the same action on the same element if it didn't work the first time.

**EXTRACTION COMPLETION:**

After calling extract, review what was extracted carefully.
If extraction shows the specific data requested, call "done" immediately.

**REASONING PROCESS:**
1. Analyze current page state
2. Check if previous action changed state - if not, try something different
3. Identify what needs to be done next
4. Handle any popups or obstacles FIRST
5. Choose the most direct action to progress
6. Explain your reasoning clearly

**REMEMBER:**
- Output ONLY JSON with no preamble
- Use single braces, not double braces
- Element indices from the provided DOM
- Search fields auto-submit after input_text
- If action didn't change state, it failed - try different approach
- For Google Flights: follow the workflow, don't navigate away mid-process
- Call "done" when task is complete or impossible to continue

Your response must be a valid JSON object and nothing else."""


EXTRACTION_SYSTEM_PROMPT = """You are an expert at extracting specific information from web pages.

Your task is to find and extract the requested information from the provided page content.

**GUIDELINES:**
1. Extract ONLY the requested information
2. Be precise and accurate
3. If information is not found, say "Information not found"
4. Don't add extra commentary
5. Format clearly and concisely
6. For "find X and Y" tasks, ensure you extract BOTH X and Y

**OUTPUT FORMAT:**
Provide a direct answer to the extraction goal based on the page content."""


def build_observation_message(
    task: str,
    current_url: str,
    current_title: str,
    elements_text: str,
    page_text: str,
    step_number: int,
    has_cookie_popup: bool = False,
    has_cloudflare: bool = False,
    has_captcha: bool = False,
    previous_action: str = None,
    error_message: str = None,
    state_change_note: str = None,
    stagnation_warning: str = None,
    wait_count: int = 0,
    max_wait_count: int = 5,
    extracted_content: str = None
) -> str:
    
    challenges = []
    if has_cookie_popup:
        challenges.append("Cookie consent popup present - use close_cookie_popup if needed")
    if has_cloudflare:
        challenges.append("Cloudflare challenge indicators (likely already cleared)")
    if has_captcha:
        challenges.append("CAPTCHA indicators detected (may already be solved)")
    
    challenge_text = "\n".join(challenges) if challenges else ""
    
    previous_context = ""
    if previous_action:
        if error_message:
            previous_context = f"\nPREVIOUS ACTION RESULT:\nAction: {previous_action}\nFAILED: {error_message}\n"
        elif state_change_note:
            previous_context = f"\nPREVIOUS ACTION RESULT:\nAction: {previous_action}\n{state_change_note}\n"
        else:
            previous_context = f"\nPREVIOUS ACTION RESULT:\nAction: {previous_action} (succeeded)\n"
        
        if extracted_content:
            preview = extracted_content[:500] if len(extracted_content) > 500 else extracted_content
            previous_context += f"\nEXTRACTED CONTENT FROM PREVIOUS ACTION:\n{preview}\n"
            if len(extracted_content) > 500:
                previous_context += f"... (showing first 500 chars of {len(extracted_content)} total)\n"
            
            task_lower = task.lower()
            if 'and' in task_lower:
                parts = task_lower.split('and')
                if len(parts) >= 2:
                    previous_context += f"\nEXTRACTION VALIDATION:\n"
                    previous_context += f"Task requests multiple items. Verify extracted content contains ALL requested items:\n"
                    for i, part in enumerate(parts, 1):
                        part_clean = part.strip()
                        previous_context += f"  {i}. {part_clean}\n"
                    previous_context += f"If all items are present in extraction above, call 'done' immediately.\n"
    
    stagnation_text = ""
    if stagnation_warning:
        stagnation_text = f"\nSTAGNATION WARNING:\n{stagnation_warning}\nYou MUST try a completely different approach now!\n"
    
    wait_warning = ""
    if wait_count >= max_wait_count:
        wait_warning = f"\nWAIT LIMIT REACHED ({wait_count}/{max_wait_count})\nYou CANNOT use wait action anymore. Take a different action!\n"
    elif wait_count >= max_wait_count - 1:
        wait_warning = f"\nWAIT WARNING: You've used wait {wait_count}/{max_wait_count} times. Use it only if absolutely necessary!\n"
    
    message = f"""TASK: {task}

STEP: {step_number}

CURRENT PAGE:
URL: {current_url}
Title: {current_title}

{challenge_text}
{previous_context}
{stagnation_text}
{wait_warning}

INTERACTIVE ELEMENTS:
{elements_text}

PAGE CONTENT (excerpt):
{page_text[:2000]}

Analyze the current state and decide the next action. Remember:
1. Check if previous action changed state - if not, try something completely different
2. Handle any popups/challenges if present
3. Use element indices from the list above
4. NEVER repeat the same failed action - adapt your strategy
5. If extraction showed the requested data, call done immediately
6. Provide clear reasoning explaining your choice

Respond with ONLY a JSON object, no other text."""
    
    return message


def build_error_recovery_message(
    task: str,
    error_count: int,
    last_errors: list,
    current_state: str
) -> str:
    
    errors_text = "\n".join([f"- {err}" for err in last_errors])
    
    message = f"""TASK: {task}

SITUATION: {error_count} consecutive errors occurred

RECENT ERRORS:
{errors_text}

CURRENT STATE:
{current_state}

RECOVERY OPTIONS:
1. Try a different approach (different element, different action)
2. Go back to previous page
3. Scroll to find different elements
4. Extract whatever information is available
5. If truly stuck, call done with success=false

Respond with ONLY a JSON object for your recovery action."""
    
    return message


def build_stagnation_recovery_message(
    task: str,
    reason: str,
    current_state: str
) -> str:
    
    message = f"""TASK: {task}

STAGNATION DETECTED:
{reason}

CURRENT STATE:
{current_state}

RECOVERY STRATEGY REQUIRED:

You are stuck in a loop! You must break out by taking a COMPLETELY DIFFERENT action.

Priority recovery strategies:

1. If on search results page, CLICK a recipe link (HIGHEST PRIORITY):
   - Search results pages don't have full recipe details
   - You must click into individual recipes to see ratings, reviews, ingredients
   - Look for recipe title links in the elements list
   - Click different recipe links to find one matching criteria

2. Try a different element:
   - If you've been clicking element N repeatedly with no effect, try a DIFFERENT element
   - Look at the page - there are likely multiple links/buttons that could help
   - Try clicking elements before or after the one that failed

3. Change navigation method:
   - If clicking failed multiple times, try scrolling to find better elements
   - Go back and search with different keywords

4. Extract and complete:
   - If you can't navigate forward, extract whatever information is currently visible
   - Call "done" with results rather than staying stuck

CRITICAL: If stuck on search results, you MUST click into a recipe page to see full details!

Respond with ONLY a JSON object for ONE recovery action that is COMPLETELY DIFFERENT from what you've been trying."""
    
    return message


def build_final_extraction_prompt(task: str, page_content: str) -> str:
    
    message = f"""The task was: {task}

Based on the current page content below, extract the answer or result:

PAGE CONTENT:
{page_content[:3000]}

Provide the most relevant information related to the task."""
    
    return message