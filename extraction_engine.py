import asyncio
import logging
import re
import json
from typing import Optional, Dict, Any, List
from playwright.async_api import Page

logger = logging.getLogger(__name__)


class ExtractionEngine:
    """
    Advanced extraction engine for handling various types of content:
    - Structured data (tables, lists)
    - Sports scores and match information
    - Research papers and academic content
    - Product listings with specifications
    - News articles and media content
    - Computational results (Wolfram Alpha, calculators)
    """
    
    def __init__(self, page: Page, llm=None):
        self.page = page
        self.llm = llm
        self.logger = logging.getLogger(__name__)
    
    async def extract_content(self, goal: str) -> Dict[str, Any]:
        """
        Main extraction method that routes to specialized extractors
        """
        try:
            goal_lower = goal.lower()
            
            # Detect extraction type
            if any(word in goal_lower for word in ['score', 'match', 'game', 'vs', 'versus', 'final score']):
                return await self.extract_sports_score(goal)
            
            elif any(word in goal_lower for word in ['paper', 'abstract', 'author', 'published', 'arxiv', 'research']):
                return await self.extract_research_paper(goal)
            
            elif any(word in goal_lower for word in ['recipe', 'ingredients', 'instructions', 'cook', 'bake']):
                return await self.extract_recipe(goal)
            
            elif any(word in goal_lower for word in ['calculate', 'compute', 'result', 'answer', 'solution', 'wolfram']):
                return await self.extract_calculation_result(goal)
            
            elif any(word in goal_lower for word in ['table', 'list all', 'all items', 'members', 'team']):
                return await self.extract_structured_data(goal)
            
            elif any(word in goal_lower for word in ['course', 'module', 'lesson', 'week', 'syllabus']):
                return await self.extract_course_info(goal)
            
            else:
                # Generic extraction
                return await self.extract_generic(goal)
                
        except Exception as e:
            self.logger.error(f"Extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Extraction failed: {str(e)}"
            }
    
    async def extract_sports_score(self, goal: str) -> Dict[str, Any]:
        """Extract sports scores and match information"""
        try:
            await asyncio.sleep(1)
            
            # Get full page text for parsing
            page_text = await self.page.text_content('body') or ""
            
            # Try to find score section
            score_sections = await self.page.query_selector_all(
                '[class*="score"], [class*="Score"], [class*="matchScore"], '
                '[class*="gameScore"], [id*="score"], [data-testid*="score"]'
            )
            
            scores_found = []
            
            # Extract from dedicated score elements
            for section in score_sections:
                try:
                    if await section.is_visible():
                        score_text = await section.text_content()
                        if score_text and re.search(r'\d+\s*[-–:]\s*\d+', score_text):
                            scores_found.append(score_text.strip())
                except:
                    continue
            
            # Look for team names and scores in tables
            tables = await self.page.query_selector_all('table')
            for table in tables:
                try:
                    table_text = await table.text_content()
                    if table_text and re.search(r'\d+\s*[-–:]\s*\d+', table_text):
                        # Extract rows
                        rows = await table.query_selector_all('tr')
                        for row in rows:
                            row_text = await row.text_content()
                            if row_text and re.search(r'\d+', row_text):
                                scores_found.append(row_text.strip())
                except:
                    continue
            
            # Pattern matching in full text for match scores
            score_patterns = [
                r'([A-Z][a-z\s]+?)\s+(\d+)\s*[-–:]\s*(\d+)\s+([A-Z][a-z\s]+)',
                r'([A-Z][a-z\s]+?)\s+(\d+)\s+([A-Z][a-z\s]+?)\s+(\d+)', 
                r'Final[:\s]*([A-Z][a-z\s]+?)\s+(\d+)\s*[-–,]\s*([A-Z][a-z\s]+?)\s+(\d+)',
            ]
            
            for pattern in score_patterns:
                matches = re.finditer(pattern, page_text)
                for match in matches:
                    scores_found.append(match.group(0))
            
            # Look for top scorer information
            scorer_info = []
            scorer_patterns = [
                r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(\d+)\s+(?:goals?|points?|runs?)',
                r'Top\s+scorer[:\s]+([A-Z][a-z\s]+)',
                r'Leading scorer[:\s]+([A-Z][a-z\s]+)',
            ]
            
            for pattern in scorer_patterns:
                matches = re.finditer(pattern, page_text, re.IGNORECASE)
                for match in matches:
                    scorer_info.append(match.group(0))
            
            # Compile results
            if scores_found or scorer_info:
                result = {
                    'success': True,
                    'extracted_content': json.dumps({
                        'scores': scores_found[:10],
                        'scorers': scorer_info[:5],
                        'full_context': page_text[:500] if len(scores_found) == 0 else None
                    }, indent=2)
                }
            else:
                # return structured text around score keywords
                score_context = []
                lines = page_text.split('\n')
                for i, line in enumerate(lines):
                    if re.search(r'\d+\s*[-–:]\s*\d+', line) or any(word in line.lower() for word in ['score', 'final', 'result']):
                        context_start = max(0, i-2)
                        context_end = min(len(lines), i+3)
                        score_context.extend(lines[context_start:context_end])
                
                result = {
                    'success': True,
                    'extracted_content': '\n'.join(score_context[:20]) if score_context else "No score information found on current page"
                }
            
            return result
            
        except Exception as e:
            self.logger.error(f"Sports score extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Score extraction failed: {str(e)}"
            }
    
    async def extract_research_paper(self, goal: str) -> Dict[str, Any]:
        """Extract research paper information"""
        try:
            await asyncio.sleep(1)
            
            paper_info = {}
            
            # Title extraction
            title_selectors = [
                'h1.title', 'h1[class*="title"]', '.article-title',
                'meta[name="citation_title"]', 'h1', 'h2.title'
            ]
            
            for selector in title_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = await self.page.query_selector(selector)
                        if elem:
                            paper_info['title'] = await elem.get_attribute('content')
                            break
                    else:
                        elem = await self.page.query_selector(selector)
                        if elem and await elem.is_visible():
                            paper_info['title'] = (await elem.text_content()).strip()
                            break
                except:
                    continue
            
            # Authors extraction
            author_selectors = [
                '.authors', '[class*="author"]', 'meta[name="citation_author"]',
                '.contrib-author', '.author-name'
            ]
            
            authors = []
            for selector in author_selectors:
                try:
                    if selector.startswith('meta'):
                        elems = await self.page.query_selector_all(selector)
                        for elem in elems:
                            author = await elem.get_attribute('content')
                            if author:
                                authors.append(author)
                    else:
                        elem = await self.page.query_selector(selector)
                        if elem:
                            author_text = await elem.text_content()
                            if author_text:
                                # Split multiple authors
                                authors.extend([a.strip() for a in re.split(r'[,;]|\sand\s', author_text)])
                                break
                except:
                    continue
            
            if authors:
                paper_info['authors'] = authors[:10]  # Limit to first 10
            
            # Abstract extraction
            abstract_selectors = [
                '.abstract', '#abstract', '[class*="abstract"]',
                'meta[name="citation_abstract"]', 'section.abstract'
            ]
            
            for selector in abstract_selectors:
                try:
                    if selector.startswith('meta'):
                        elem = await self.page.query_selector(selector)
                        if elem:
                            paper_info['abstract'] = await elem.get_attribute('content')
                            break
                    else:
                        elem = await self.page.query_selector(selector)
                        if elem:
                            abstract_text = await elem.text_content()
                            if abstract_text and len(abstract_text) > 50:
                                paper_info['abstract'] = abstract_text.strip()
                                break
                except:
                    continue
            
            # Publication date
            date_selectors = [
                'meta[name="citation_publication_date"]',
                '.published-date', '[class*="pubdate"]',
                'meta[property="article:published_time"]'
            ]
            
            for selector in date_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem:
                        if selector.startswith('meta'):
                            paper_info['published_date'] = await elem.get_attribute('content')
                        else:
                            paper_info['published_date'] = (await elem.text_content()).strip()
                        break
                except:
                    continue
            
            # DOI or arXiv ID
            page_url = self.page.url
            if 'arxiv.org' in page_url:
                arxiv_match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+)', page_url)
                if arxiv_match:
                    paper_info['arxiv_id'] = arxiv_match.group(1)
            
            doi_match = re.search(r'10\.\d{4,}/[^\s]+', await self.page.text_content('body'))
            if doi_match:
                paper_info['doi'] = doi_match.group(0)
            
            # If we have meaningful data, return it
            if paper_info and (paper_info.get('title') or paper_info.get('abstract')):
                return {
                    'success': True,
                    'extracted_content': json.dumps(paper_info, indent=2)
                }
            else:
                # Fallback: extract main content
                main_content = await self.page.text_content('main, article, .content, #content')
                return {
                    'success': True,
                    'extracted_content': (main_content or "")[:1000]
                }
                
        except Exception as e:
            self.logger.error(f"Paper extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Paper extraction failed: {str(e)}"
            }
    
    async def extract_calculation_result(self, goal: str) -> Dict[str, Any]:
        """Extract calculation/computation results (Wolfram Alpha, calculators, etc)"""
        try:
            await asyncio.sleep(2)
            
            # Wolfram Alpha specific
            if 'wolframalpha.com' in self.page.url:
                return await self._extract_wolfram_result(goal)
            
            # Generic computational result extraction
            result_selectors = [
                '[class*="result"]', '[class*="Result"]', '[class*="answer"]',
                '[class*="Answer"]', '[class*="solution"]', '[class*="output"]',
                '#result', '#answer', '.output', '[data-testid*="result"]'
            ]
            
            results = []
            for selector in result_selectors:
                try:
                    elems = await self.page.query_selector_all(selector)
                    for elem in elems:
                        if await elem.is_visible():
                            text = await elem.text_content()
                            if text and len(text.strip()) > 0:
                                results.append(text.strip())
                except:
                    continue
            
            if results:
                return {
                    'success': True,
                    'extracted_content': '\n\n'.join(results[:5])
                }
            else:
                # Fallback: look for numbers and formulas in main content
                page_text = await self.page.text_content('body')
                return {
                    'success': True,
                    'extracted_content': page_text[:800]
                }
                
        except Exception as e:
            self.logger.error(f"Calculation extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Calculation extraction failed: {str(e)}"
            }
    
    async def _extract_wolfram_result(self, goal: str) -> Dict[str, Any]:
        """Specialized extraction for Wolfram Alpha results"""
        try:
            await asyncio.sleep(2)
            
            pods = await self.page.query_selector_all('[class*="_1D42U"], section, .pod')
            
            result_data = {}
            
            for pod in pods:
                try:
                    # Get pod title
                    title_elem = await pod.query_selector('h2, h3, [class*="title"]')
                    title = ""
                    if title_elem:
                        title = await title_elem.text_content()
                    
                    # Get pod content
                    content = await pod.text_content()
                    
                    if content and len(content.strip()) > 0:
                        # Store by title if available
                        key = title.strip() if title else f"result_{len(result_data)}"
                        result_data[key] = content.strip()
                except:
                    continue
            
            if result_data:
                return {
                    'success': True,
                    'extracted_content': json.dumps(result_data, indent=2)
                }
            
            # Fallback: extract all visible text from main content area
            main_content_selectors = ['main', '#main', '[role="main"]', '.main-content']
            for selector in main_content_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem:
                        content = await elem.text_content()
                        if content:
                            return {
                                'success': True,
                                'extracted_content': content.strip()[:1500]
                            }
                except:
                    continue
            
            return {
                'success': True,
                'extracted_content': await self.page.text_content('body')[:1000]
            }
            
        except Exception as e:
            self.logger.error(f"Wolfram extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Wolfram extraction failed: {str(e)}"
            }
    
    async def extract_recipe(self, goal: str) -> Dict[str, Any]:
        """Extract recipe information with ingredients and instructions"""
        try:
            await asyncio.sleep(1)
            
            recipe_data = {}
            
            # Recipe title
            title_selectors = ['h1', '.recipe-title', '[class*="recipe"] h1']
            for selector in title_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem:
                        recipe_data['title'] = (await elem.text_content()).strip()
                        break
                except:
                    continue
            
            # Ingredients
            ingredient_selectors = [
                '.recipe-ingredients', '[class*="ingredients"]',
                '#ingredients', '[itemprop="recipeIngredient"]'
            ]
            
            ingredients = []
            for selector in ingredient_selectors:
                try:
                    parent = await self.page.query_selector(selector)
                    if parent:
                        # Get list items or children
                        items = await parent.query_selector_all('li, p, span')
                        for item in items:
                            text = await item.text_content()
                            if text and len(text.strip()) > 2:
                                ingredients.append(text.strip())
                        if ingredients:
                            break
                except:
                    continue
            
            if ingredients:
                recipe_data['ingredients'] = ingredients
            
            # Instructions
            instruction_selectors = [
                '.recipe-instructions', '[class*="instructions"]',
                '#instructions', '[itemprop="recipeInstructions"]'
            ]
            
            instructions = []
            for selector in instruction_selectors:
                try:
                    parent = await self.page.query_selector(selector)
                    if parent:
                        items = await parent.query_selector_all('li, p, div[class*="step"]')
                        for item in items:
                            text = await item.text_content()
                            if text and len(text.strip()) > 10:
                                instructions.append(text.strip())
                        if instructions:
                            break
                except:
                    continue
            
            if instructions:
                recipe_data['instructions'] = instructions
            
            # Rating and reviews
            try:
                rating_elem = await self.page.query_selector('[class*="rating"], [itemprop="ratingValue"]')
                if rating_elem:
                    rating_text = await rating_elem.text_content()
                    rating_match = re.search(r'(\d+(?:\.\d+)?)', rating_text)
                    if rating_match:
                        recipe_data['rating'] = rating_match.group(1)
            except:
                pass
            
            if recipe_data:
                return {
                    'success': True,
                    'extracted_content': json.dumps(recipe_data, indent=2)
                }
            else:
                # Fallback
                return {
                    'success': True,
                    'extracted_content': await self.page.text_content('main, article, .recipe, #recipe')[:1000]
                }
                
        except Exception as e:
            self.logger.error(f"Recipe extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Recipe extraction failed: {str(e)}"
            }
    
    async def extract_structured_data(self, goal: str) -> Dict[str, Any]:
        """Extract structured data from tables and lists"""
        try:
            await asyncio.sleep(1)
            
            # Extract all tables
            tables = await self.page.query_selector_all('table')
            table_data = []
            
            for i, table in enumerate(tables):
                try:
                    if await table.is_visible():
                        table_text = await table.text_content()
                        if table_text:
                            table_data.append({
                                'table_index': i,
                                'content': table_text.strip()
                            })
                except:
                    continue
            
            # Extract lists
            lists = await self.page.query_selector_all('ul, ol')
            list_data = []
            
            for i, list_elem in enumerate(lists):
                try:
                    if await list_elem.is_visible():
                        items = await list_elem.query_selector_all('li')
                        if len(items) > 0:
                            list_items = []
                            for item in items:
                                text = await item.text_content()
                                if text:
                                    list_items.append(text.strip())
                            if list_items:
                                list_data.append({
                                    'list_index': i,
                                    'items': list_items
                                })
                except:
                    continue
            
            result = {}
            if table_data:
                result['tables'] = table_data[:5]
            if list_data:
                result['lists'] = list_data[:5]
            
            if result:
                return {
                    'success': True,
                    'extracted_content': json.dumps(result, indent=2)
                }
            else:
                return await self.extract_generic(goal)
                
        except Exception as e:
            self.logger.error(f"Structured data extraction error: {e}")
            return await self.extract_generic(goal)
    
    async def extract_course_info(self, goal: str) -> Dict[str, Any]:
        """Extract course/module information (Coursera, educational sites)"""
        try:
            await asyncio.sleep(1)
            
            course_info = {}
            
            # Course title
            title_selectors = ['h1', '.course-title', '[class*="course"] h1']
            for selector in title_selectors:
                try:
                    elem = await self.page.query_selector(selector)
                    if elem:
                        course_info['title'] = (await elem.text_content()).strip()
                        break
                except:
                    continue
            
            # Modules/Weeks
            module_selectors = [
                '[class*="module"]', '[class*="week"]',
                '[class*="syllabus"] li', '[class*="curriculum"] li'
            ]
            
            modules = []
            for selector in module_selectors:
                try:
                    elems = await self.page.query_selector_all(selector)
                    for elem in elems[:15]:  # Limit to 15 modules
                        if await elem.is_visible():
                            text = await elem.text_content()
                            if text and len(text.strip()) > 3:
                                modules.append(text.strip())
                    if modules:
                        break
                except:
                    continue
            
            if modules:
                course_info['modules'] = modules
            
            # Duration/Lessons count
            try:
                page_text = await self.page.text_content('body')
                duration_match = re.search(r'(\d+)\s+(?:weeks?|months?|hours?)', page_text, re.IGNORECASE)
                if duration_match:
                    course_info['duration'] = duration_match.group(0)
                
                lessons_match = re.search(r'(\d+)\s+(?:lessons?|lectures?|videos?)', page_text, re.IGNORECASE)
                if lessons_match:
                    course_info['lessons'] = lessons_match.group(0)
            except:
                pass
            
            if course_info:
                return {
                    'success': True,
                    'extracted_content': json.dumps(course_info, indent=2)
                }
            else:
                return await self.extract_generic(goal)
                
        except Exception as e:
            self.logger.error(f"Course extraction error: {e}")
            return await self.extract_generic(goal)
    
    async def extract_generic(self, goal: str) -> Dict[str, Any]:
        """Generic extraction fallback"""
        try:
            # Use LLM to extract if available
            if self.llm:
                page_content = await self.page.text_content('body')
                
                extraction_prompt = f"""Extract the following information from the page:

Goal: {goal}

Page Content:
{page_content[:3000]}

Provide a clear, structured answer to the goal."""
                
                extracted = await self.llm.extract_content(extraction_prompt, page_content[:5000])
                
                return {
                    'success': True,
                    'extracted_content': extracted
                }
            else:
                # Simple text extraction
                main_content = await self.page.text_content('main, article, .content, #content, body')
                
                return {
                    'success': True,
                    'extracted_content': (main_content or "")[:1000]
                }
                
        except Exception as e:
            self.logger.error(f"Generic extraction error: {e}")
            return {
                'success': False,
                'error': str(e),
                'extracted_content': f"Extraction failed: {str(e)}"
            }