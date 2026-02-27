import re
import logging
from typing import List, Dict, Optional, Tuple
from constraint_parser import Constraint, ConstraintType

logger = logging.getLogger(__name__)


class UniversalResultValidator:
    
    @classmethod
    def validate_results(cls, results: List[Dict], constraints: List[Constraint]) -> Tuple[List[Dict], Dict]:
        if not constraints:
            return results, {'passed': len(results), 'failed': 0, 'validation_applied': False}
        
        valid_results = []
        failed_results = []
        
        for result in results:
            is_valid, reasons = cls._validate_single_result(result, constraints)
            if is_valid:
                result['validation_passed'] = True
                valid_results.append(result)
            else:
                result['validation_passed'] = False
                result['validation_failure_reasons'] = reasons
                failed_results.append(result)
        
        validation_summary = {
            'passed': len(valid_results),
            'failed': len(failed_results),
            'validation_applied': True,
            'total_constraints': len(constraints)
        }
        
        return valid_results, validation_summary
    
    @classmethod
    def _validate_single_result(cls, result: Dict, constraints: List[Constraint]) -> Tuple[bool, List[str]]:
        failure_reasons = []
        
        for constraint in constraints:
            passed, reason = cls._check_constraint(result, constraint)
            if not passed:
                failure_reasons.append(reason)
        
        return len(failure_reasons) == 0, failure_reasons
    
    @classmethod
    def _check_constraint(cls, result: Dict, constraint: Constraint) -> Tuple[bool, str]:
        if constraint.type in [ConstraintType.RATING_MIN, ConstraintType.SCORE_MIN]:
            return cls._check_rating_min(result, constraint.value)
        elif constraint.type in [ConstraintType.RATING_MAX, ConstraintType.SCORE_MAX]:
            return cls._check_rating_max(result, constraint.value)
        elif constraint.type == ConstraintType.PRICE_MAX:
            return cls._check_price_max(result, constraint.value)
        elif constraint.type == ConstraintType.PRICE_MIN:
            return cls._check_price_min(result, constraint.value)
        elif constraint.type == ConstraintType.TIME_MAX:
            return cls._check_time_max(result, constraint.value)
        elif constraint.type == ConstraintType.TIME_MIN:
            return cls._check_time_min(result, constraint.value)
        elif constraint.type == ConstraintType.REVIEW_MIN:
            return cls._check_review_min(result, constraint.value)
        elif constraint.type == ConstraintType.DIETARY:
            return cls._check_dietary(result, constraint.value)
        else:
            return True, ""
    
    @classmethod
    def _check_rating_min(cls, result: Dict, min_rating: float) -> Tuple[bool, str]:
        rating_fields = ['rating', 'score', 'stars', 'review_score']
        
        for field in rating_fields:
            if field in result:
                rating_value = cls._extract_number(result[field])
                if rating_value is not None:
                    if rating_value >= min_rating:
                        return True, ""
                    else:
                        return False, f"Rating {rating_value} < {min_rating}"
        
        result_text = ' '.join(str(v) for v in result.values())
        rating_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:star|rating|out of|/)', result_text, re.IGNORECASE)
        if rating_match:
            rating_value = float(rating_match.group(1))
            if rating_value >= min_rating:
                return True, ""
            else:
                return False, f"Rating {rating_value} < {min_rating}"
        
        return True, ""
    
    @classmethod
    def _check_rating_max(cls, result: Dict, max_rating: float) -> Tuple[bool, str]:
        rating_fields = ['rating', 'score', 'stars', 'review_score']
        
        for field in rating_fields:
            if field in result:
                rating_value = cls._extract_number(result[field])
                if rating_value is not None:
                    if rating_value <= max_rating:
                        return True, ""
                    else:
                        return False, f"Rating {rating_value} > {max_rating}"
        
        return True, ""
    
    @classmethod
    def _check_price_max(cls, result: Dict, max_price: float) -> Tuple[bool, str]:
        price_fields = ['price', 'cost', 'amount']
        
        for field in price_fields:
            if field in result:
                price_value = cls._extract_price(result[field])
                if price_value is not None:
                    if price_value <= max_price:
                        return True, ""
                    else:
                        return False, f"Price ${price_value} > ${max_price}"
        
        result_text = ' '.join(str(v) for v in result.values())
        price_match = re.search(r'[\$€£¥₹]\s*(\d+(?:\.\d{2})?)|(\d+(?:\.\d{2})?)\s*[\$€£¥₹]', result_text)
        if price_match:
            price_value = float(price_match.group(1) or price_match.group(2))
            if price_value <= max_price:
                return True, ""
            else:
                return False, f"Price ${price_value} > ${max_price}"
        
        return True, ""
    
    @classmethod
    def _check_price_min(cls, result: Dict, min_price: float) -> Tuple[bool, str]:
        price_fields = ['price', 'cost', 'amount']
        
        for field in price_fields:
            if field in result:
                price_value = cls._extract_price(result[field])
                if price_value is not None:
                    if price_value >= min_price:
                        return True, ""
                    else:
                        return False, f"Price ${price_value} < ${min_price}"
        
        return True, ""
    
    @classmethod
    def _check_time_max(cls, result: Dict, max_time: int) -> Tuple[bool, str]:
        time_fields = ['time', 'duration', 'prep_time', 'cook_time', 'total_time']
        
        for field in time_fields:
            if field in result:
                time_value = cls._extract_time(result[field])
                if time_value is not None:
                    if time_value <= max_time:
                        return True, ""
                    else:
                        return False, f"Time {time_value}min > {max_time}min"
        
        result_text = ' '.join(str(v) for v in result.values())
        time_match = re.search(r'(\d+)\s*(?:min|minute|minutes|hour|hours|hrs)', result_text, re.IGNORECASE)
        if time_match:
            time_value = int(time_match.group(1))
            if 'hour' in result_text.lower():
                time_value *= 60
            if time_value <= max_time:
                return True, ""
            else:
                return False, f"Time {time_value}min > {max_time}min"
        
        return True, ""
    
    @classmethod
    def _check_time_min(cls, result: Dict, min_time: int) -> Tuple[bool, str]:
        time_fields = ['time', 'duration', 'prep_time', 'cook_time', 'total_time']
        
        for field in time_fields:
            if field in result:
                time_value = cls._extract_time(result[field])
                if time_value is not None:
                    if time_value >= min_time:
                        return True, ""
                    else:
                        return False, f"Time {time_value}min < {min_time}min"
        
        return True, ""
    
    @classmethod
    def _check_review_min(cls, result: Dict, min_reviews: int) -> Tuple[bool, str]:
        review_fields = ['reviews', 'review_count', 'ratings', 'rating_count']
        
        for field in review_fields:
            if field in result:
                review_value = cls._extract_number(result[field])
                if review_value is not None:
                    if review_value >= min_reviews:
                        return True, ""
                    else:
                        return False, f"Reviews {review_value} < {min_reviews}"
        
        result_text = ' '.join(str(v) for v in result.values())
        review_match = re.search(r'(\d+)\s*(?:review|reviews|rating|ratings)', result_text, re.IGNORECASE)
        if review_match:
            review_value = int(review_match.group(1))
            if review_value >= min_reviews:
                return True, ""
            else:
                return False, f"Reviews {review_value} < {min_reviews}"
        
        return True, ""
    
    @classmethod
    def _check_dietary(cls, result: Dict, dietary_requirement: str) -> Tuple[bool, str]:
        result_text = ' '.join(str(v) for v in result.values()).lower()
        dietary_lower = dietary_requirement.lower()
        
        if dietary_lower in result_text:
            return True, ""
        else:
            return False, f"Does not contain '{dietary_requirement}'"
    
    @classmethod
    def _extract_number(cls, text: str) -> Optional[float]:
        if isinstance(text, (int, float)):
            return float(text)
        
        if not isinstance(text, str):
            return None
        
        match = re.search(r'(\d+(?:\.\d+)?)', text)
        if match:
            return float(match.group(1))
        
        return None
    
    @classmethod
    def _extract_price(cls, text: str) -> Optional[float]:
        if isinstance(text, (int, float)):
            return float(text)
        
        if not isinstance(text, str):
            return None
        
        price_match = re.search(r'[\$€£¥₹]\s*(\d+(?:\.\d{2})?)|(\d+(?:\.\d{2})?)\s*[\$€£¥₹]', text)
        if price_match:
            return float(price_match.group(1) or price_match.group(2))
        
        return None
    
    @classmethod
    def _extract_time(cls, text: str) -> Optional[int]:
        if isinstance(text, int):
            return text
        
        if not isinstance(text, str):
            return None
        
        time_match = re.search(r'(\d+)\s*(?:min|minute|minutes)', text, re.IGNORECASE)
        if time_match:
            return int(time_match.group(1))
        
        hour_match = re.search(r'(\d+)\s*(?:hour|hours|hrs)', text, re.IGNORECASE)
        if hour_match:
            return int(hour_match.group(1)) * 60
        
        return None