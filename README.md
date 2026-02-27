# WebAgent

A web automation agent that takes a plain English task and completes it in a real browser. You write the task, point it at a URL, and it handles the rest like clicking, typing, scrolling, dealing with popups, logging in when needed, and extracting the answer.

No CSS selectors. No XPath. No scripts that break when a site redesigns. The agent reads the page at every step and figures out what to do from scratch each time.

Built with Gemini 2.0 Flash (vision + text) and Playwright.

---

## Quickstart

Python 3.10+

```bash
git clone https://github.com/Kernelsphere-web/kernelsphere.git
cd kernelsphere
pip install -r requirements.txt
playwright install chromium
```

```env
# .env
GOOGLE_API_KEY=your_gemini_api_key
```

```bash
python main.py "Find the starting price of the MacBook Air M2" \
  --start-url "https://www.apple.com"
```

That's it. The result lands in `final_output.json`.

---

## How it works

The agent screenshots the page and runs a JavaScript extraction to collect all visible interactive elements like buttons, inputs, links, dropdowns, anything clickable. Up to 200 elements get indexed and scored (submit buttons and search fields rank higher than decorative links). That list plus the screenshot goes to Gemini, which returns the next action as JSON: which element to interact with, what to do, and why.

Playwright executes it. Then the agent checks whether anything actually changed in URL, DOM hash, new dialogs. If nothing changed, it knows the action failed. Two consecutive identical actions with no effect and the agent stops repeating itself and tries a different path.

When the target information is on screen, a five-strategy extractor runs: semantic selectors first, then structural patterns, viewport scanning, regex over page text, and finally a full-page pass. The first strategy that returns a valid result wins. That result gets validated against the original task, and if it passes, the agent writes the final answer and stops. Every step follows the same cycle

---

## Running tasks

```bash
# Basic
python main.py "Find a vegetarian lasagna with at least 4-star rating" \
  --start-url "https://www.allrecipes.com"

# Headless, more steps
python main.py "Find papers on transformer attention published this month" \
  --start-url "https://arxiv.org" \
  --headless \
  --max-steps 25

# Google Flights (has a dedicated handler for date pickers and autocomplete)
python main.py "Cheapest non-stop from Hyderabad to Berlin on March 15" \
  --start-url "https://www.google.com/flights" \
  --use-browserbase \
  --max-steps 30
```

**All flags**

| Flag | Default | What it does |
|---|---|---|
| `--start-url` | required | Where to begin |
| `--max-steps` | 30 | Give up after this many steps |
| `--headless` | false | No browser window |
| `--model` | gemini-2.0-flash-exp | Gemini model to use |
| `--output` | final_output.json | Path for results |
| `--use-browserbase` | false | Cloud browser that handles most CAPTCHAs |
| `--browserbase-timeout` | 600 | Session timeout in seconds (max 21600) |
| `--use-proxy` | false | Rotate proxies per session |
| `--proxy-country` | none | Preferred country code, e.g. `US` |
| `--enable-proxy-health-check` | false | Background monitoring of proxy pool |
| `--viewport-width` | 1280 | Browser width |
| `--viewport-height` | 720 | Browser height |

---

## What gets handled automatically

**Login flows.** The agent detects login pages, fills credentials, and continues. It also tracks whether it's already logged in so it doesn't attempt a second login on the same session.

**Email OTP.** If a site sends a verification code to email, the agent polls the inbox over IMAP, extracts the code, and types it in. Tested on Gmail. Most IMAP providers should work but haven't been tested.

**CAPTCHAs.** Stealth browser config (masked `navigator.webdriver`, randomized user agents, spoofed canvas) handles a lot of them passively. For sites that get through that, Browserbase cloud sessions solve CAPTCHAs automatically. For the rest, there's a manual fallback with a configurable wait time.

**Popups and cookie banners.** A pattern-based handler dismisses cookie consent dialogs, promotional modals, and overlay close buttons before each extraction step. It tracks what's already been dismissed so it doesn't try the same popup twice.

**Stagnation.** If the agent repeats the same action on the same element three times with no page change, it detects the loop and switches to a recovery prompt that pushes it toward a different approach.

---



## Parallel runs

Default is 3 concurrent workers. Change it up with `--concurrency`, but each browser instance needs roughly 300–500 MB, so don't go higher than your machine can handle.

```bash
python parallel_runner.py \
  --tasks-file data/tasks.jsonl \
  --output-dir results \
  --concurrency 5
```

Split a large file first if needed:

```bash
python batch_processor.py split data/tasks.jsonl --batch-size 50
python batch_processor.py split data/tasks.jsonl --by-website
python batch_processor.py aggregate results/run1 results/run2 --output combined.json
```

Watch a run in progress:

```bash
python progress_monitor.py --mode monitor     # live, every 10s
python progress_monitor.py --mode failures    # what failed and why
python progress_monitor.py --mode eta         # time left estimate
```

---

## Output format

```json
{
  "task": "Find the starting price of the MacBook Air M2",
  "start_url": "https://www.apple.com",
  "success": true,
  "total_steps": 7,
  "final": {
    "final_answer": "MacBook Air M2 starts at $1,099."
  },
  "steps": [
    {
      "step": 1,
      "url": "https://www.apple.com",
      "actions": [{ "action": "click_element", "success": true, "url_changed": true }]
    }
  ]
}
```

---

## Evaluation

```bash
python auto_eval.py \
  --process_dir results \
  --api_key your_openai_key \
  --api_model gpt-4-vision-preview \
  --max_attached_imgs 3
```



## License

MIT see [LICENSE](LICENSE)
