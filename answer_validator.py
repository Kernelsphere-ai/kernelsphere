import re
import json
from typing import Dict, Any, Tuple, List


class AnswerValidator:
    
    GENERIC_FAIL_PHRASES = [
        "information not found",
        "unable to find",
        "could not find",
        "couldn't find",
        "not available",
        "no data",
        "no results",
        "failed to",
        "error occurred",
        "cannot find",
        "can't find",
        "extraction failed",
        "no content",
        "not accessible",
        "not visible",
        "task failed",
        "i apologize",
        "i'm unable",
        "i cannot"
    ]
    
    POSITIVE_INDICATORS = [
        "iphone",
        "release",
        "model",
        "series",
        "year",
        "price",
        "feature",
        "specification",
        "display",
        "camera",
        "processor",
        "storage",
        "recipe",
        "ingredient",
        "rating",
        "review",
        "product",
        "available",
        "search result",
        "found"
    ]
    
    @classmethod
    def validate_answer(cls, answer: str, task_question: str) -> Tuple[bool, Dict[str, Any]]:
        validation_result = {
            "is_valid": False,
            "reason": "",
            "confidence": 0.0,
            "checks_passed": []
        }
        
        if not answer or len(answer.strip()) < 5:
            validation_result["reason"] = "Answer too short or empty"
            return False, validation_result
        
        answer_lower = answer.lower().strip()
        task_lower = task_question.lower().strip()
        
        fail_phrase_found = False
        for phrase in cls.GENERIC_FAIL_PHRASES:
            if phrase in answer_lower:
                if len(answer_lower) < 50:
                    validation_result["reason"] = f"Contains failure phrase: '{phrase}' and answer is very short"
                    return False, validation_result
                else:
                    fail_phrase_found = True
        
        if not fail_phrase_found:
            validation_result["checks_passed"].append("No failure phrases")
        
        try:
            data = json.loads(answer)
            if isinstance(data, dict):
                if "error" in data or "Error" in data:
                    if isinstance(data.get("error"), str) and len(data.get("error", "")) > 0:
                        validation_result["reason"] = "Contains error field in JSON with error message"
                        return False, validation_result
                
                empty_count = 0
                total_fields = 0
                
                for key, value in data.items():
                    if key.lower() in ['error', 'status', 'success']:
                        continue
                    total_fields += 1
                    if not value or value in ["", "null", "None", "N/A", "not found", "information not found"]:
                        empty_count += 1
                
                if total_fields > 0 and empty_count == total_fields:
                    validation_result["reason"] = "All fields are empty or contain failure values"
                    return False, validation_result
                
                if total_fields > 0 and empty_count / total_fields > 0.7:
                    validation_result["reason"] = f"Too many empty fields: {empty_count}/{total_fields}"
                    return False, validation_result
                
                if total_fields > 0:
                    validation_result["checks_passed"].append(f"JSON has {total_fields - empty_count}/{total_fields} populated fields")
        except:
            pass
        
        key_terms = cls._extract_key_terms(task_lower)
        
        found_terms = 0
        for term in key_terms:
            if term in answer_lower:
                found_terms += 1
        
        if key_terms and found_terms >= len(key_terms) * 0.5:
            validation_result["checks_passed"].append(f"Found {found_terms}/{len(key_terms)} key terms from question")
        
        if "iphone" in task_lower or "phone" in task_lower:
            phone_indicators = ["iphone", "model", "release", "year", "series", "pro", "plus", "max"]
            phone_found = sum(1 for ind in phone_indicators if ind in answer_lower)
            if phone_found >= 2:
                validation_result["checks_passed"].append("Phone information detected")
        
        if "recipe" in task_lower:
            recipe_indicators = ["ingredient", "cup", "tablespoon", "teaspoon", "minute", "cook", "bake", "recipe"]
            recipe_found = sum(1 for ind in recipe_indicators if ind in answer_lower)
            if recipe_found >= 2:
                validation_result["checks_passed"].append("Recipe information detected")
        
        if any(word in task_lower for word in ["rating", "star", "review"]):
            has_rating = bool(re.search(r'\d+(?:\.\d+)?\s*(?:star|rating|reviews?)|rating:\s*\d+(?:\.\d+)?', answer_lower))
            if has_rating:
                validation_result["checks_passed"].append("Rating information found")
        
        if any(word in task_lower for word in ["price", "cost", "how much"]):
            has_price = bool(re.search(r'[\$£€¥]|price|cost|\d+\.\d{2}', answer_lower))
            if has_price:
                validation_result["checks_passed"].append("Price information found")
        
        if "search" in task_lower or "find" in task_lower or "latest" in task_lower:
            search_indicators = ["found", "search", "result", "latest", "current", "available", "released"]
            search_found = sum(1 for ind in search_indicators if ind in answer_lower)
            if search_found >= 1:
                validation_result["checks_passed"].append("Search/find information present")
        
        positive_count = 0
        for indicator in cls.POSITIVE_INDICATORS:
            if indicator in answer_lower:
                positive_count += 1
        
        if positive_count >= 2:
            validation_result["checks_passed"].append(f"Contains {positive_count} positive indicators")
        
        word_count = len(answer.split())
        if word_count >= 10:
            validation_result["checks_passed"].append(f"Adequate length: {word_count} words")
        elif word_count < 5:
            validation_result["reason"] = f"Answer too short: only {word_count} words"
            return False, validation_result
        
        if len(validation_result["checks_passed"]) >= 2:
            validation_result["is_valid"] = True
            validation_result["confidence"] = min(1.0, len(validation_result["checks_passed"]) * 0.2)
            validation_result["reason"] = "Validation checks passed"
            return True, validation_result
        else:
            validation_result["reason"] = f"Insufficient validation checks passed: {len(validation_result['checks_passed'])}/2 required"
            return False, validation_result
    
    @classmethod
    def _extract_key_terms(cls, text: str) -> List[str]:
        skip_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
            'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'should', 'could', 'may', 'might', 'must', 'can', 'go', 'get',
            'make', 'see', 'know', 'take', 'find', 'give', 'tell', 'ask', 'work',
            'seem', 'feel', 'try', 'leave', 'call', 'search', 'return', 'user',
            'please', 'thank', 'thanks'
        }
        
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
        key_terms = [w for w in words if w not in skip_words]
        return list(set(key_terms))[:10]
    
    @classmethod
    def select_best_answer_from_history(cls, steps: list, task_question: str) -> Tuple[str, int]:
        best_answer = ""
        best_score = -1
        best_step = -1
        
        for step in steps:
            for action in step.get("actions", []):
                if action.get("action") == "extract" and action.get("extracted_content"):
                    content = action["extracted_content"]
                    
                    is_valid, validation = cls.validate_answer(content, task_question)
                    
                    if is_valid:
                        score = validation["confidence"] * 100
                        if score > best_score:
                            best_score = score
                            best_answer = content
                            best_step = step.get("step", -1)
        
        return best_answer, best_step