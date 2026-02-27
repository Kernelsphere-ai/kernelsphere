import re
import json
from typing import Any, Dict


# Common problematic character mappings
CHAR_REPLACEMENTS = {
    # Windows-1252 characters that break UTF-8
    '\x93': '"',  # Left double quotation mark
    '\x94': '"',  # Right double quotation mark
    '\x91': "'",  # Left single quotation mark
    '\x92': "'",  # Right single quotation mark
    '\x96': '–',  # En dash
    '\x97': '—',  # Em dash
    '\x85': '…',  # Horizontal ellipsis
    '\x95': '•',  # Bullet
    '\x80': '€',  # Euro sign
    '\x82': '‚',  # Single low-9 quotation mark
    '\x83': 'ƒ',  # Latin small letter f with hook
    '\x84': '„',  # Double low-9 quotation mark
    '\x86': '†',  # Dagger
    '\x87': '‡',  # Double dagger
    '\x88': 'ˆ',  # Modifier letter circumflex accent
    '\x89': '‰',  # Per mille sign
    '\x8a': 'Š',  # Latin capital letter S with caron
    '\x8b': '‹',  # Single left-pointing angle quotation mark
    '\x8c': 'Œ',  # Latin capital ligature OE
    '\x8e': 'Ž',  # Latin capital letter Z with caron
    '\x98': '˜',  # Small tilde
    '\x99': '™',  # Trade mark sign
    '\x9a': 'š',  # Latin small letter s with caron
    '\x9b': '›',  # Single right-pointing angle quotation mark
    '\x9c': 'œ',  # Latin small ligature oe
    '\x9e': 'ž',  # Latin small letter z with caron
    '\x9f': 'Ÿ',  # Latin capital letter Y with diaeresis
}


def normalize_text(text: str) -> str:
    """
    Normalize text to clean UTF-8
    
    Args:
        text: Input text (possibly with encoding issues)
        
    Returns:
        Clean UTF-8 text
    """
    if not text:
        return text
    
    # Replace known problematic characters
    for bad, good in CHAR_REPLACEMENTS.items():
        text = text.replace(bad, good)
    
    # Ensure valid UTF-8 encoding
    try:
        text = text.encode('utf-8', errors='replace').decode('utf-8')
    except Exception:
        # If that fails, try latin-1 which never fails
        try:
            text = text.encode('latin-1', errors='replace').decode('utf-8', errors='replace')
        except Exception:
            # Last resort: just strip non-ASCII
            text = ''.join(char for char in text if ord(char) < 128)
    
    # Remove control characters (except newlines and tabs)
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    
    # Normalize whitespace (but preserve newlines)
    lines = text.split('\n')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in lines]
    text = '\n'.join(lines)
    text = text.strip()
    
    return text


def normalize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively normalize all strings in a dictionary
    
    Args:
        data: Dictionary with potentially problematic text
        
    Returns:
        Dictionary with normalized text
    """
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        # Normalize key
        if isinstance(key, str):
            key = normalize_text(key)
        
        # Normalize value
        if isinstance(value, str):
            result[key] = normalize_text(value)
        elif isinstance(value, dict):
            result[key] = normalize_dict(value)
        elif isinstance(value, list):
            result[key] = normalize_list(value)
        else:
            result[key] = value
    
    return result


def normalize_list(data: list) -> list:
    """
    Recursively normalize all strings in a list
    """
    if not isinstance(data, list):
        return data
    
    result = []
    for item in data:
        if isinstance(item, str):
            result.append(normalize_text(item))
        elif isinstance(item, dict):
            result.append(normalize_dict(item))
        elif isinstance(item, list):
            result.append(normalize_list(item))
        else:
            result.append(item)
    
    return result


def safe_json_dumps(data: Any, **kwargs) -> str:
    """
    JSON dumps with automatic text normalization
    
    Args:
        data: Data to serialize
        **kwargs: Arguments to json.dumps
        
    Returns:
        JSON string with clean encoding
    """
    # Normalize data first
    if isinstance(data, dict):
        data = normalize_dict(data)
    elif isinstance(data, list):
        data = normalize_list(data)
    elif isinstance(data, str):
        data = normalize_text(data)
    
    # Ensure ensure_ascii is False to preserve unicode
    kwargs['ensure_ascii'] = kwargs.get('ensure_ascii', False)
    
    try:
        return json.dumps(data, **kwargs)
    except (UnicodeEncodeError, UnicodeDecodeError):
        # use ensure_ascii=True
        kwargs['ensure_ascii'] = True
        return json.dumps(data, **kwargs)


def safe_json_loads(text: str) -> Any:
    """
    JSON loads with automatic text normalization
    
    Args:
        text: JSON string (possibly with encoding issues)
        
    Returns:
        Parsed data with normalized text
    """
    # Normalize the input text first
    text = normalize_text(text)
    
    try:
        data = json.loads(text)
        
        # Normalize the parsed data
        if isinstance(data, dict):
            return normalize_dict(data)
        elif isinstance(data, list):
            return normalize_list(data)
        else:
            return data
            
    except json.JSONDecodeError as e:
        # Try to fix common JSON issues
        
        # Remove any BOM
        text = text.lstrip('\ufeff')
        
        # Try again
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return normalize_dict(data)
            elif isinstance(data, list):
                return normalize_list(data)
            else:
                return data
        except:
            raise e  # Re-raise original error


def detect_and_fix_encoding(text: str) -> str:
    """
    Detect common encoding issues and fix them
    
    Args:
        text: Text with potential encoding issues
        
    Returns:
        Fixed text
    """
    if not text:
        return text
    
    # Check for mojibake patterns
    mojibake_fixes = {
        'â€™': "'",      # Mojibake apostrophe
        'â€œ': '"',      # Mojibake left quote
        'â€': '"',       # Mojibake right quote
        'â€"': '—',      # Mojibake em dash
        'â€"': '–',      # Mojibake en dash
        'Â·': '·',       # Mojibake middle dot
        'Â ': ' ',       # Mojibake nbsp
        'â€¦': '…',      # Mojibake ellipsis
        'â€¢': '•',      # Mojibake bullet
    }
    
    for bad, good in mojibake_fixes.items():
        text = text.replace(bad, good)
    
    return normalize_text(text)


def prepare_subprocess_result(result: Dict[str, Any]) -> str:
    """
    Prepare result dictionary for subprocess output
    Ensures no encoding issues in JSON
    
    Args:
        result: Result dictionary
        
    Returns:
        Clean JSON string
    """
    # Normalize all text in the result
    result = normalize_dict(result)
    
    # Convert to JSON with clean encoding
    return safe_json_dumps(result)


