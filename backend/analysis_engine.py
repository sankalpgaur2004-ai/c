from openai import OpenAI
import pandas as pd
from typing import List, Dict, Any, Optional
import logging
import json
import re

logger = logging.getLogger(__name__)

def _persona_system_suffix(persona: Optional[str]) -> str:
    """Return persona-specific system prompt modifier."""
    if not persona:
        return ""

    modifiers = {
        "executive": "\n\nYou are presenting to a C-suite pharmaceutical executive. Focus on strategic implications, market position, revenue impact, and high-level KPIs. Avoid technical jargon and use boardroom-ready language. Emphasize trends and business risk.",
        "sales_manager": "\n\nYou are a pharmaceutical sales analytics specialist. Focus on team performance metrics, territory comparisons, target attainment, and pipeline health. Use specific numbers and percentages. Highlight outliers and areas needing attention.",
        "field_rep": "\n\nYou are a sales assistant helping a field representative. Focus on account-level details, HCP engagement data, and product performance at account level. Provide concrete, actionable insights for their next customer visit.",
        "analyst": "\n\nYou are a pharmaceutical data analyst. Include methodology notes, statistical observations, data quality remarks, and edge cases. Be thorough and precise. Include confidence caveats where appropriate.",
    }
    return modifiers.get(persona, "")

class AnalysisEngine:
    def __init__(self, api_key: str):
        # OpenAI client
        self.client = OpenAI(api_key=api_key)
        # Using gpt-4o for better formatting and analysis quality
        self.model = "gpt-4o"
    
    def generate_combined_analysis(
        self,
        question: str,
        sql_query: str,
        data: pd.DataFrame,
        previous_question: str = None,
        previous_summary: str = None,
        persona: str = None
    ) -> Dict[str, Any]:
        """
        OPTIMIZED: Generate summary AND follow-up questions in ONE API call
        
        Replaces:
        - generate_summary() +
        - generate_follow_up_questions()
        
        Returns: {
            "summary": str,
            "follow_up_questions": List[str]
        }
        """
        data_summary = f"""
Data shape: {data.shape[0]} rows, {data.shape[1]} columns
Columns: {', '.join(data.columns.tolist())}

First 5 rows:
{data.head().to_string()}

Data types:
{data.dtypes.to_string()}
"""
        
        prompt = f"""
Analyze the SQL query results and provide a CONCISE response directly answering the user's question.

User Question: {question}
SQL Query: {sql_query}

Data Summary:
{data_summary}

IMPORTANT: Treat this as a standalone query. Do NOT reference or assume any previous context.
"""
        
        # Intentionally ignore previous context - each query is independent
        # if previous_question and previous_summary:
        #     prompt += f"""
        # Previous Context:
        # Q: {previous_question}
        # A: {previous_summary}
        # """
        
        prompt += """
The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself and decide. Don't guess from keywords; just read it
the way a person would (e.g. "in points", "5 reasons", "briefly", "in a paragraph",
"elaborate" all mean something plain and obvious).

Provide your response in the following JSON format:

{{
  "format": "list" or "prose",
  "summary": <SEE BELOW — depends on "format">,
  "follow_up_questions": [
    "Follow-up question 1?",
    "Follow-up question 2?",
    "Follow-up question 3?",
    "Follow-up question 4?"
  ]
}}

HOW TO FILL "format" AND "summary":
- Set "format" to "list" if the user asked for points, a list, bullets, numbered items,
  or "N reasons/things/ways/points" etc. Then "summary" MUST be a JSON ARRAY OF STRINGS —
  one string per point, plain text, use **bold** for key numbers/terms within each string.
  Do NOT put dashes or numbers in the strings yourself — the app adds those. Example:
  "summary": ["**West Region** is fastest at 55.75 days (17% faster than East).", "**North Region** averages 71.04 days (6% slower than East).", "**South Region** has the longest time at 73.30 days (9% slower than East)."]
- Otherwise set "format" to "prose". Then "summary" is a SINGLE markdown string, using
  **bold** for key numbers/metrics/conclusions, structured as a short report:
  "**Direct Answer:** ...\\n\\n**Key Findings:**\\n- point one\\n- point two\\n\\n**Business Insight:** ..."
  (only use "- " list lines for the Key Findings section itself, not the format field)

If the user asked for BRIEF/short: keep it to 1-3 sentences or up to 3 list items — cut
anything non-essential. If they asked to ELABORATE/go in-depth: include more supporting
numbers and context, in either format.

RULES:
- NO repetition of the question or data structure details
- NO generic statements like "the data shows" or "based on the analysis"
- Include specific numbers, percentages, or metrics
- Focus ONLY on what directly answers the user's question

FOLLOW-UP QUESTIONS:
- Generate 4 short, simple follow-up questions
- Each one must build directly on THIS question and THIS result — reference the same metric, table, or dimension that was just returned, not a new unrelated topic
- Phrase them the way a person would naturally ask them next, in plain everyday language — no jargon, no compound/multi-part questions
- Do NOT invent broad "industry" or "strategic" questions that aren't grounded in the data just shown

Return ONLY the JSON object, no additional text.
"""

        try:
            system_prompt = "You are a pharmaceutical business analyst. Provide concise, structured answers with specific metrics. Avoid repetition and generic statements. Focus on pharma-specific insights."
            system_prompt += _persona_system_suffix(persona)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000,
                response_format={"type": "json_object"},  # Force valid JSON output — prevents literal newlines inside strings
            )
            
            raw_response = response.choices[0].message.content.strip()
            
            # Parse JSON response
            result = self._parse_combined_response(raw_response)
            
            logger.info(f"Combined analysis generated: summary + {len(result['follow_up_questions'])} follow-ups")
            return result
            
        except Exception as e:
            logger.error(f"Combined analysis generation failed: {e}")
            # Fallback to separate calls if combined fails
            logger.warning("Falling back to separate analysis calls")
            return self._fallback_separate_analysis(question, sql_query, data, previous_question, previous_summary)
    
    def _parse_combined_response(self, raw: str) -> Dict[str, Any]:
        """Parse JSON response from combined analysis"""
        
        # Remove markdown code blocks if present
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        cleaned = re.sub(r'```\s*$', '', cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()
        
        # Try to find JSON object
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            json_str = match.group()
            
            # Pre-parse cleanup: escape unescaped control characters within string values
            # This handles cases where OpenAI includes literal newlines/tabs in JSON strings
            json_str = self._escape_json_control_chars(json_str)
            
            # Try multiple JSON repair strategies
            for attempt, repair_fn in enumerate([
                lambda s: s,  # Try as-is first
                self._repair_json_trailing_comma,
                self._repair_json_quotes,
                self._repair_json_newlines,  # Additional newline repair
            ]):
                try:
                    repaired = repair_fn(json_str)
                    result = json.loads(repaired)
                    
                    # Validate structure
                    if 'summary' in result and 'follow_up_questions' in result:
                        # Ensure follow_up_questions is a list
                        if isinstance(result['follow_up_questions'], list):
                            summary = self._clean_summary(result['summary'])
                            return {
                                'answered':             result.get('answered', True),
                                'format':              result.get('format', 'list' if isinstance(summary, list) else 'prose'),
                                'summary':              summary,
                                'used_chunk_ids':      result.get('used_chunk_ids'),
                                'follow_up_questions': result['follow_up_questions'][:6]  # Limit to 6
                            }
                except json.JSONDecodeError as e:
                    if attempt == 0:
                        logger.error(f"JSON parse error: {e}")
                    continue
        
        # If parsing fails, try to extract manually
        logger.warning("JSON parsing failed, attempting manual extraction")
        extracted = self._manual_extraction(raw)
        # Clean the summary
        extracted['summary'] = self._clean_summary(extracted['summary'])
        extracted.setdefault('format', 'prose')
        return extracted
    
    def _clean_summary(self, summary):
        """Clean summary text of any JSON artifacts or structural elements.
        Handles both the normal string case (prose) and the list-of-strings
        case (points format) — a list is already clean structured data from
        the JSON parse, so each item just gets the same string cleanup."""
        if isinstance(summary, list):
            return [self._clean_summary_text(item) for item in summary if str(item).strip()]
        return self._clean_summary_text(summary)

    def _clean_summary_text(self, summary: str) -> str:
        """Clean summary text of any JSON artifacts or structural elements"""
        if not summary:
            return summary
        
        # Remove JSON structural elements that might have leaked through
        # Remove opening/closing braces at start/end
        summary = summary.strip()
        if summary.startswith('{'):
            summary = summary[1:].strip()
        if summary.endswith('}'):
            summary = summary[:-1].strip()
        
        # Remove "summary": or 'summary': prefix
        summary = re.sub(r'^["\']?summary["\']?\s*:\s*["\']?', '', summary, flags=re.IGNORECASE)
        
        # Remove trailing JSON elements like `, "follow_up_questions":`
        summary = re.sub(r',?\s*["\']?follow_up_questions["\']?\s*:\s*\[.*$', '', summary, flags=re.DOTALL)
        
        # Clean up escaped characters
        summary = summary.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
        
        # Remove leading/trailing quotes
        summary = summary.strip('"\'')
        
        return summary.strip()
    
    def _repair_json_trailing_comma(self, json_str: str) -> str:
        """Remove trailing commas before closing brackets/braces"""
        # Remove trailing commas: ,] or ,}
        repaired = re.sub(r',\s*([}\]])', r'\1', json_str)
        return repaired
    
    def _repair_json_quotes(self, json_str: str) -> str:
        """Try to fix common quote issues in JSON"""
        repaired = json_str
        # Fix unquoted keys
        repaired = re.sub(r'(\{|\,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', repaired)
        return repaired
    
    def _repair_json_newlines(self, json_str: str) -> str:
        """Replace literal newlines in string values with escaped newlines"""
        # This is a more aggressive approach - replace all literal newlines with \\n
        # except those between JSON elements
        lines = json_str.split('\n')
        result = []
        in_string = False
        
        for i, line in enumerate(lines):
            # Track quote state
            stripped = line.strip()
            
            # Simple heuristic: if line ends with ", or starts with key pattern, it's a JSON line break
            if stripped.endswith('",') or stripped.endswith('"') or stripped.startswith('"') or stripped.endswith('{') or stripped.endswith('[') or stripped.endswith('},') or stripped.endswith('],') or stripped == '}' or stripped == ']':
                result.append(line)
            else:
                # This is likely a continuation of a string value - join with \\n
                if result:
                    result[-1] = result[-1].rstrip() + '\\n' + line.lstrip()
                else:
                    result.append(line)
        
        return '\n'.join(result)
    
    def _escape_json_control_chars(self, json_str: str) -> str:
        """
        Escape unescaped control characters (newlines, tabs, etc) within JSON strings.
        This prevents "Invalid control character" JSON decode errors from Groq responses.
        
        Handles literal newlines/tabs inside string values by escaping them properly.
        """
        try:
            # Split by quotes to process only inside string values
            # Pattern: matches content between unescaped quotes
            parts = []
            in_string = False
            escaped = False
            i = 0
            
            while i < len(json_str):
                char = json_str[i]
                
                # Track if we're inside a string
                if char == '"' and not escaped:
                    in_string = not in_string
                    parts.append(char)
                    escaped = False
                elif char == '\\' and not escaped:
                    # Escape sequence start - pass through the backslash and mark next char as escaped
                    parts.append(char)
                    escaped = True
                elif escaped:
                    # This char follows a backslash — it's part of an escape sequence, pass through as-is
                    parts.append(char)
                    escaped = False
                elif in_string:
                    # Inside string, not escaped - escape any raw control characters
                    if char == '\n':
                        parts.append('\\n')
                    elif char == '\r':
                        parts.append('\\r')
                    elif char == '\t':
                        parts.append('\\t')
                    elif char == '\b':
                        parts.append('\\b')
                    elif char == '\f':
                        parts.append('\\f')
                    elif ord(char) < 32:  # Other control characters
                        parts.append(f'\\u{ord(char):04x}')
                    else:
                        parts.append(char)
                else:
                    parts.append(char)
                    escaped = False
                
                i += 1
            
            return ''.join(parts)
        except Exception as e:
            logger.warning(f"Error escaping JSON control chars: {e} — proceeding with original")
            return json_str
    
    def _manual_extraction(self, text: str) -> Dict[str, Any]:
        """Manually extract summary and questions if JSON parsing fails"""
        
        summary = ""
        questions = []
        
        # Method 1: Find "summary" key and extract its value more robustly
        # Handle case where the value spans multiple lines with literal newlines
        summary_start = text.find('"summary"')
        if summary_start != -1:
            # Find the colon and opening quote
            colon_pos = text.find(':', summary_start)
            if colon_pos != -1:
                # Find opening quote after colon
                open_quote = text.find('"', colon_pos + 1)
                if open_quote != -1:
                    # Find the closing quote - needs to handle escaped quotes
                    pos = open_quote + 1
                    while pos < len(text):
                        if text[pos] == '"' and text[pos-1] != '\\':
                            # Found closing quote
                            summary = text[open_quote + 1:pos]
                            break
                        pos += 1
                    # Unescape
                    summary = summary.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
        
        # Method 2: Extract follow-up questions
        questions_match = re.search(r'"follow_up_questions"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if questions_match:
            questions_text = questions_match.group(1)
            # Extract individual quoted strings
            individual_questions = re.findall(r'"([^"]+)"', questions_text)
            questions = [q.replace('\\n', ' ').replace('\\"', '"').strip() for q in individual_questions]
            questions = [q for q in questions if q and len(q) > 10]
        
        if summary and questions:
            return {'summary': summary, 'follow_up_questions': questions[:6]}
        
        # Method 3: If we have summary but no questions, generate generic ones
        if summary:
            return {
                'summary': summary,
                'follow_up_questions': [
                    "How does this compare to previous periods?",
                    "What factors might be influencing these results?",
                    "Are there regional variations in this data?",
                    "What trends can we identify?"
                ]
            }
        
        # Method 4: Try to extract clean text without JSON wrapper
        # Look for actual content between the structural JSON elements
        clean_text = re.sub(r'[{}"\[\]]', '', text)
        clean_text = re.sub(r'summary\s*:', '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'follow_up_questions\s*:', '', clean_text, flags=re.IGNORECASE)
        clean_text = clean_text.strip()
        
        if clean_text and len(clean_text) > 50:
            return {
                'summary': clean_text[:2000],
                'follow_up_questions': [
                    "How does this compare to previous periods?",
                    "What factors might be influencing these results?",
                    "Are there regional variations in this data?",
                    "What trends can we identify?"
                ]
            }
        
        # Last resort: return raw text
        return {
            'summary': text[:2000] if len(text) > 2000 else text,
            'follow_up_questions': [
                "How do these results compare to the previous quarter?",
                "What regional variations can we observe?",
                "What trends can we identify over time?",
                "How do different segments perform?"
            ]
        }
    
    def _fallback_separate_analysis(
        self,
        question: str,
        sql_query: str,
        data: pd.DataFrame,
        previous_question: str = None,
        previous_summary: str = None
    ) -> Dict[str, Any]:
        """Fallback to separate API calls if combined fails"""
        try:
            summary = self.generate_summary(question, sql_query, data, previous_question, previous_summary)
            follow_ups = self.generate_follow_up_questions(question, data, previous_question)
            
            return {
                'summary': summary,
                'follow_up_questions': follow_ups
            }
        except Exception as e:
            logger.error(f"Fallback analysis also failed: {e}")
            return {
                'summary': f"Analysis completed. {data.shape[0]} records found.",
                'follow_up_questions': ["How do these results compare to previous periods?"]
            }

    def generate_summary(self, question: str, sql_query: str, data: pd.DataFrame,
                          previous_question: str = None, previous_summary: str = None, persona: str = None) -> str:
        data_summary = f"""
Data shape: {data.shape[0]} rows, {data.shape[1]} columns
Columns: {', '.join(data.columns.tolist())}

First 5 rows:
{data.head().to_string()}

Data types:
{data.dtypes.to_string()}
"""
        prompt = f"""
Analyze the SQL query results and provide a CONCISE, user-focused summary.

User Question: {question}
SQL Query: {sql_query}

Data Summary:
{data_summary}
"""
        if previous_question and previous_summary:
            prompt += f"""
Previous Context:
Q: {previous_question}
A: {previous_summary}
"""
        prompt += """
OUTPUT FORMAT (follow this structure):

**Direct Answer:**
[1-2 sentence direct answer to the question]

**Key Findings:**
• [Finding 1 with specific numbers/metrics]
• [Finding 2 with specific numbers/metrics]
• [Finding 3 with specific numbers/metrics]

**Business Insight:**
[1-2 sentences on pharma business implications]

RULES:
- NO repetition of the question
- NO generic statements like "the data shows"
- Include specific numbers and metrics
- Maximum 5-6 sentences total
- Focus ONLY on what answers the question

Keep it concise and to the point.
"""
        try:
            system_prompt = "You are a pharmaceutical business analyst. Provide concise, structured answers with specific metrics. Avoid repetition and generic statements. Focus on pharma-specific insights."
            system_prompt += _persona_system_suffix(persona)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=600,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return f"Analysis completed. {data.shape[0]} records found. Please review the data table for detailed insights."

    def generate_follow_up_questions(self, question: str, data: pd.DataFrame,
                                     previous_question: str = None) -> List[str]:
        data_summary = f"""
Data shape: {data.shape[0]} rows, {data.shape[1]} columns
Columns: {', '.join(data.columns.tolist())}

First 3 rows:
{data.head(3).to_string()}
"""
        prompt = f"""
Based on the query results, generate 4 short, simple follow-up questions that build directly on THIS question and THIS data.

User Question: {question}
Data Summary:
{data_summary}
"""
        if previous_question:
            prompt += f"""
Previous Question: {previous_question}
"""
        prompt += """
Return ONLY the questions, one per line, without numbering or bullets.
Each question must reference the same columns, table, or metric shown above — no unrelated or generic topics.
Keep them short and plain, the way a person would naturally ask them next. No compound or multi-part questions.
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a pharmaceutical business analyst generating short, simple follow-up questions grounded in the exact data just shown."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.5,
                max_tokens=250,
            )
            questions_text = response.choices[0].message.content.strip()
            questions_list = [q.strip() for q in questions_text.split('\n') if q.strip()]
            return questions_list[:6]
        except Exception as e:
            logger.error(f"Follow-up questions generation failed: {e}")
            return ["How do these results compare to the previous period?"]

    def generate_document_response(
        self,
        question: str,
        context_chunks: List[Dict[str, Any]],
        previous_question: Optional[str] = None,
        previous_summary: Optional[str] = None,
        persona: Optional[str] = None,
        is_summary: bool = False,
        conversation_history: list = None,
    ) -> Dict[str, Any]:
        """
        Generate answer + follow-up questions using retrieved document chunks.
        Uses a narrative summary prompt for broad questions, and a precise
        lookup prompt for specific data questions.
        """
        if not context_chunks:
            return {
                "answered": False,
                "format": "prose",
                "summary": (
                    "No relevant context found in the uploaded documents for this question. "
                    "Try rephrasing or check that the right document is uploaded."
                ),
                "follow_up_questions": [
                    "Can you summarize the key points from the uploaded documents?",
                    "Which document sections are most relevant to this topic?",
                ],
            }

        # Build context block grouped by source — label each chunk with its
        # real filename (and page, when known) so the model can cite the
        # actual source instead of a meaningless "Doc 1"/"Doc 2" index. Each
        # chunk also gets a stable CHUNK_ID so the model can report back
        # exactly which ones it actually drew on (see "used_chunk_ids"
        # below) — the citations shown to the user should reflect what was
        # actually used to write the answer, not everything that happened
        # to be retrieved.
        doc_context_lines: List[str] = []
        valid_chunk_ids: set = set()
        for i, chunk in enumerate(context_chunks, 1):
            filename = chunk.get("metadata", {}).get("filename", "Unknown")
            page     = chunk.get("metadata", {}).get("page")
            score    = chunk.get("relevance_score", 0)
            text     = (chunk.get("text") or "").strip()
            chunk_id = chunk.get("chunk_id") or f"chunk_{i}"
            valid_chunk_ids.add(chunk_id)
            source_label = f"{filename}, page {page}" if page else filename
            doc_context_lines.append(
                f"[CHUNK_ID: {chunk_id} | SOURCE: {source_label} | relevance={score:.2f}]\n{text}"
            )

        context_block = "\n\n".join(doc_context_lines)

        # Build conversation history block
        history_block = ""
        if conversation_history:
            recent = conversation_history[-5:]
            lines = ["CONVERSATION HISTORY (use only if current question refers to prior answers):"]
            for i, entry in enumerate(reversed(recent), 1):
                q = entry.get("question", "")
                s = entry.get("summary", "")
                src = entry.get("sourceFilter") or entry.get("source_filter", "")
                lines.append(f"[Turn -{i}] Source: {src} | Q: {q}")
                if s:
                    lines.append(f"  A: {s[:250]}")
            history_block = "\n".join(lines) + "\n\n"

        if is_summary:
            # ── Summary mode: narrative, human-readable, structured ──────────
            system_prompt = """You are a knowledgeable analyst summarizing content from various sources.
Sources may include web pages, PowerPoint slides, PDFs, documents, or plain text.

Web page content is structured with headings (## Heading) and paragraphs.
PowerPoint chart data is formatted as: SeriesName for Period: Value
Table data is formatted as pipe-separated rows.

Your job: Write a clear, well-structured NARRATIVE SUMMARY in plain English.
DO NOT dump raw data — interpret it into meaningful sentences.
By default, prefer flowing paragraphs with key facts embedded over bullet points for everything — UNLESS the user's question explicitly asks for points/a list/bullets, in which case use a real markdown list instead.
Structure your response with bold section headers where appropriate.
If the content is from a web page, summarize what the page is about and the key information it contains."""

            system_prompt += _persona_system_suffix(persona)

            prompt = f"""{history_block}User request: {question}

--- SOURCE CONTENT ---
{context_block}
--- END CONTENT ---

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "5 reasons", "briefly", "in a
paragraph", "elaborate" all mean something plain and obvious). Don't guess from
keyword lists; just read it like a person would.

Return ONLY a JSON object with this structure:

{{
  "answered": true or false — false ONLY if the source content has nothing relevant to this question,
  "format": "list" or "prose",
  "summary": <SEE BELOW — depends on "format">,
  "used_chunk_ids": ["<CHUNK_ID values you actually drew information from to write this answer>"],
  "follow_up_questions": [
    "Short follow-up question 1",
    "Short follow-up question 2",
    "Short follow-up question 3",
    "Short follow-up question 4"
  ]
}}

CRITICAL for "used_chunk_ids": list ONLY the exact CHUNK_ID values (from the
"[CHUNK_ID: ...]" labels above) for chunks whose content you actually used to
write "summary". If a chunk was in the context but you didn't draw on it (e.g.
it's from a different, unrelated document), leave its ID out — this list is
what the app shows the user as "Sources", so it must be accurate, not just
everything that was provided.

HOW TO FILL "format" AND "summary":
- Set "format" to "list" if the user asked for points, a list, bullets, numbered
  items, or "N reasons/things/ways/points" etc. Then "summary" MUST be a JSON ARRAY
  OF STRINGS — one string per point, plain text, use **bold** for key terms within
  each string. Do NOT add dashes/numbers yourself — the app adds those.
- Otherwise set "format" to "prose". Then "summary" is a SINGLE well-written
  narrative markdown string — flowing paragraphs with key facts embedded, **bold**
  for key terms, bold section headers if it helps organize a longer answer.

If the user asked for BRIEF/short: keep it tight — 1-3 sentences or up to 3 list
items. If they asked to ELABORATE/go in-depth: include more supporting detail and
context, in either format.

DEFAULT LENGTH (when the user didn't specify brief/elaborate — e.g. a plain
"summarize this document" or "give me an overview"): aim for a well-rounded
summary that's proportional to the source material, not a fixed word count.
- Cover every major theme/section actually present in the content above — don't
  fixate on just the first chunk or one sub-topic and call it done.
- Long enough that someone who hasn't read the source would understand what it's
  about and its key takeaways; short enough that it's still a summary, not a
  retelling. As a loose guide, that's usually a few short paragraphs (roughly
  120-350 words) for a single document — less if the source material itself is
  thin, more only if it genuinely covers several distinct topics.
- Never pad with filler sentences ("this document discusses...") just to hit a
  length — every sentence should carry real information from the source.

HIGHLIGHTING: bold (**text**) every key entity, number, technical term, and
conclusion as it comes up — not just one phrase per paragraph. Someone skimming
only the bolded words should still be able to follow the main point.

Writing rules:
- Write like a human analyst explaining findings, NOT a data dump
- Highlight key facts, trends, and notable findings
- For web content: explain what the page covers and extract key information
- If nothing in the source content is relevant: set "answered" to false and
  "summary" to "I can only answer questions related to the loaded knowledge base."
  (still respecting "format" — as a single-item list if format is "list")

Follow-up question rules:
- Each question must be about something that actually appears in the source content above — no generic or unrelated topics
- Keep them short, plain, and simple, like a person naturally asking "what about X next?"
- No compound or multi-part questions
"""
        else:
            # ── Lookup mode: precise, specific data extraction ───────────────
            system_prompt = """You are an analyst answering precise questions from loaded source content.
Sources may include web pages, documents, slides, or any text content.

Web page content has headings and paragraphs. Look for the specific answer anywhere in the context.
Table data is pipe-separated. Chart data: SeriesName for Period: Value.

Your job: find and extract the specific information the user asked for and answer directly.
Do NOT say data is unavailable if the answer appears anywhere in the context.
Do NOT repeat the question back. Be concise and specific.
For web page questions: look for the relevant section/heading and extract the answer."""

            system_prompt += _persona_system_suffix(persona)

            prompt = f"""{history_block}User question: {question}

--- SOURCE CONTENT ---
{context_block}
--- END CONTENT ---

The user's own question is the ONLY source of truth for how they want the answer
formatted — read it yourself (e.g. "in points", "5 reasons", "briefly", "in a
paragraph", "elaborate" all mean something plain and obvious). Don't guess from
keyword lists; just read it like a person would.

Return ONLY a JSON object:

{{
  "answered": true or false — false ONLY if the answer genuinely does not appear anywhere in the source content,
  "format": "list" or "prose",
  "summary": <SEE BELOW — depends on "format">,
  "used_chunk_ids": ["<CHUNK_ID values you actually drew information from to write this answer>"],
  "follow_up_questions": [
    "Short follow-up question 1",
    "Short follow-up question 2",
    "Short follow-up question 3",
    "Short follow-up question 4"
  ]
}}

CRITICAL for "used_chunk_ids": list ONLY the exact CHUNK_ID values (from the
"[CHUNK_ID: ...]" labels above) for chunks whose content you actually used to
write "summary". If a chunk was in the context but irrelevant (e.g. from a
different document), leave its ID out — this list is what the app shows the
user as "Sources", so it must be accurate, not just everything provided.

HOW TO FILL "format" AND "summary":
- Set "format" to "list" if the user asked for points, a list, bullets, numbered
  items, or "N reasons/things/ways/points" etc. Then "summary" MUST be a JSON ARRAY
  OF STRINGS — one string per point, plain text, use **bold** for key metrics/terms
  within each string. Do NOT add dashes/numbers yourself — the app adds those.
- Otherwise set "format" to "prose". Then "summary" is a SINGLE string: a direct
  answer with specific numbers from the context, **bold** for key metrics. When
  citing a source, use the exact filename (and page, if shown) from that chunk's
  "[SOURCE: ...]" label above — e.g. "(Resume.pdf, page 2)" — never say "Doc 1" or
  "Doc 2", those aren't real names.

If the user asked for BRIEF/short: keep it tight — 1-3 sentences or up to 3 list
items. If they asked to ELABORATE/go in-depth: include more supporting detail.

Critical rules:
- Answer directly using numbers from the context — do not hedge if the data is there
- For chart data lines like "Onco360 Enrollments for Oct-23: 25", read the value (25) and use it
- For summary lines like "Onco360 Enrollments summary: Dec-22=4, ..., Oct-23=25", use the full series
- Set "answered" to false only if the data truly does not appear anywhere in the context
- Unrelated questions (weather, sports, etc.): set "answered" to false and "summary" to "I can only answer questions related to the loaded knowledge base."

Follow-up question rules:
- Each question must be about something that actually appears in the source content above — no generic or unrelated topics
- Keep them short, plain, and simple, like a person naturally asking "what about X next?"
- No compound or multi-part questions
"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.2 if is_summary else 0.1,
                max_tokens=1800 if is_summary else 1200,
                response_format={"type": "json_object"},
            )
            raw_response = response.choices[0].message.content.strip()
            return self._parse_combined_response(raw_response)
        except Exception as e:
            logger.error(f"Document response generation failed: {e}")
            return {
                "answered": False,
                "format": "prose",
                "summary": "Document analysis failed. Please try again.",
                "follow_up_questions": [],
            }