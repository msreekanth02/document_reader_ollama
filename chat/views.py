"""
MAC AI UTILITY Chat - Views
Robust file search and Ollama LLM integration
"""

import os
import base64
import json
import subprocess
import PyPDF2
import requests
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# Ollama Configuration
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

# Supported file extensions for different operations
TEXT_EXTENSIONS = {'.txt', '.md', '.py', '.js', '.html', '.css', '.json', '.xml', '.csv', '.log', '.sh', '.yaml', '.yml'}
DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx'}
ALL_SEARCHABLE = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS | {'.pdf', '.txt', '.doc', '.docx', '.xlsx', '.pptx'}


def chat_view(request):
    """Render the main chat interface"""
    return render(request, 'chat/chat.html')


@csrf_exempt
def chat_message_api(request):
    """
    Handle chat messages with optional file attachment.
    Sends the message (and file content if provided) to Ollama for response.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    
    user_message = request.POST.get('message', '').strip()
    file = request.FILES.get('file')
    response_format = request.POST.get('format', 'default')
    document_content = ''
    
    # Process uploaded file
    if file:
        try:
            document_content = extract_file_content(file)
        except Exception as e:
            return JsonResponse({'error': f'Failed to read file: {str(e)}'}, status=400)
    
    # Require at least a message or document
    if not user_message and not document_content:
        return JsonResponse({'error': 'Please provide a message or attach a document.'}, status=400)
    
    # Format instructions based on user preference
    format_instructions = get_format_instructions(response_format)
    
    # Build prompt for Ollama
    if document_content:
        # Limit document content to avoid token overflow
        max_content_length = 15000
        if len(document_content) > max_content_length:
            document_content = document_content[:max_content_length] + "\n\n[Document truncated due to length...]"
        
        prompt = f"""You are a helpful AI assistant. A user has provided a document and asked a question about it.

USER QUESTION: {user_message if user_message else "Please summarize this document."}

DOCUMENT CONTENT:
{document_content}

{format_instructions}

Please provide a helpful, accurate answer based on the document content above. If the answer cannot be found in the document, say so clearly."""
    else:
        prompt = f"""You are a helpful AI assistant. Please answer the following question accurately and helpfully.

USER QUESTION: {user_message}

{format_instructions}

Please provide a clear and informative response."""
    
    # Call Ollama API
    try:
        model_response = call_ollama(prompt)
        return JsonResponse({'response': model_response})
    except Exception as e:
        return JsonResponse({'error': f'Ollama error: {str(e)}'}, status=500)


def get_format_instructions(response_format):
    """Get formatting instructions based on user preference"""
    format_map = {
        'default': "Format your response appropriately using markdown. Use headings, lists, tables, or code blocks as needed.",
        'bullets': "FORMAT REQUIREMENT: Present your response as bullet points (unordered list). Use - or * for each point. Make each point clear and concise.",
        'numbered': "FORMAT REQUIREMENT: Present your response as a numbered list. Start each main point with a number (1., 2., 3., etc.). Use sub-numbers for nested points.",
        'table': "FORMAT REQUIREMENT: Present your response in a markdown table format where possible. Use | for columns and - for header separators. Include headers.",
        'brief': "FORMAT REQUIREMENT: Be very brief and concise. Give only the essential information in 2-3 sentences maximum. No lengthy explanations.",
        'detailed': "FORMAT REQUIREMENT: Provide a comprehensive, detailed explanation. Include examples, context, and thorough coverage of the topic. Use headings to organize sections.",
        'code': "FORMAT REQUIREMENT: Focus on code and technical details. Use code blocks with proper syntax highlighting (```language). Include comments and explanations within code."
    }
    return format_map.get(response_format, format_map['default'])


def extract_file_content(file):
    """Extract text content from various file types"""
    filename = file.name.lower()
    file.seek(0)
    
    if filename.endswith('.pdf'):
        # Extract text from PDF
        pdf_reader = PyPDF2.PdfReader(file)
        text_parts = []
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n".join(text_parts)
    else:
        # Try to read as text
        file.seek(0)
        return file.read().decode('utf-8', errors='ignore')


def call_ollama(prompt):
    """Call Ollama API and return the response"""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": True
    }
    
    response = requests.post(OLLAMA_API_URL, json=payload, timeout=120, stream=True)
    response.raise_for_status()
    
    full_response = ''
    for line in response.iter_lines():
        if line:
            try:
                data = json.loads(line.decode('utf-8'))
                full_response += data.get('response', '')
            except json.JSONDecodeError:
                continue
    
    return full_response if full_response else "No response from model."


@csrf_exempt
def file_search_api(request):
    """
    Advanced file and folder search across the Mac.
    Supports:
    - Filename search: "document"
    - File type filter: "pdf", "txt", etc.
    - Content search: "content:keyword"
    - Combined: "report pdf" or "content:budget xlsx"
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    
    try:
        data = json.loads(request.body)
        query = data.get('query', '').strip().lower()
        
        if not query:
            return JsonResponse({'error': 'Please provide a search query.'}, status=400)
        
        # Parse query components
        keywords = []
        file_extensions = []
        content_search = None
        
        for part in query.split():
            part = part.strip('"\'')
            if not part:
                continue
            
            # Check for content search
            if part.startswith('content:'):
                content_search = part.split(':', 1)[1]
                continue
            
            # Check for file extension filter
            if part in ['pdf', 'txt', 'doc', 'docx', 'csv', 'xlsx', 'json', 'md', 'py', 'js', 'html']:
                file_extensions.append('.' + part)
                continue
            
            keywords.append(part)
        
        # Use macOS Spotlight (mdfind) for fast searching
        results = search_with_spotlight(keywords, file_extensions, content_search)
        
        # Fallback to manual search if Spotlight returns no results
        if not results:
            results = search_manual(keywords, file_extensions, content_search)
        
        return JsonResponse({'results': results})
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def search_with_spotlight(keywords, file_extensions, content_search):
    """Use macOS Spotlight (mdfind) for fast file searching"""
    results = []
    home_dir = os.path.expanduser('~')
    
    try:
        # Build mdfind query
        query_parts = []
        
        if keywords:
            # Search for files with keywords in name
            name_query = ' '.join(keywords)
            query_parts.append(f'kMDItemDisplayName == "*{name_query}*"cd')
        
        if file_extensions:
            ext_conditions = ' || '.join([f'kMDItemFSName == "*.{ext[1:]}"' for ext in file_extensions])
            if ext_conditions:
                query_parts.append(f'({ext_conditions})')
        
        if content_search:
            query_parts.append(f'kMDItemTextContent == "*{content_search}*"cd')
        
        # Execute mdfind
        if query_parts:
            mdfind_query = ' && '.join(query_parts) if len(query_parts) > 1 else query_parts[0]
            cmd = ['mdfind', '-onlyin', home_dir, mdfind_query]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                paths = result.stdout.strip().split('\n')
                for path in paths[:30]:  # Limit results
                    if path and os.path.exists(path):
                        is_dir = os.path.isdir(path)
                        results.append({
                            'type': 'directory' if is_dir else 'file',
                            'path': path
                        })
    except Exception:
        pass  # Fallback to manual search
    
    return results


def search_manual(keywords, file_extensions, content_search):
    """Manual file system search as fallback"""
    results = []
    home_dir = os.path.expanduser('~')
    
    # Directories to skip for performance
    skip_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 
                 'Library', '.Trash', '.cache', 'Cache', 'Caches'}
    
    try:
        for root, dirs, files in os.walk(home_dir, topdown=True):
            # Skip hidden and system directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in skip_dirs]
            
            # Search directories
            if keywords:
                for dir_name in dirs:
                    if any(kw in dir_name.lower() for kw in keywords):
                        results.append({
                            'type': 'directory',
                            'path': os.path.join(root, dir_name)
                        })
                        if len(results) >= 30:
                            return results
            
            # Search files
            for file_name in files:
                file_path = os.path.join(root, file_name)
                file_lower = file_name.lower()
                
                # Skip hidden files
                if file_name.startswith('.'):
                    continue
                
                # Apply extension filter
                if file_extensions:
                    if not any(file_lower.endswith(ext) for ext in file_extensions):
                        continue
                
                # Check filename match
                name_match = not keywords or any(kw in file_lower for kw in keywords)
                
                # Check content match
                content_match = True
                if content_search:
                    content_match = False
                    try:
                        _, ext = os.path.splitext(file_lower)
                        if ext in TEXT_EXTENSIONS:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                content = f.read(50000)  # Read first 50KB
                                if content_search in content.lower():
                                    content_match = True
                    except Exception:
                        continue
                
                if name_match and content_match:
                    results.append({
                        'type': 'file',
                        'path': file_path
                    })
                
                if len(results) >= 30:
                    return results
    except Exception:
        pass
    
    return results


@csrf_exempt
def list_dir_api(request):
    """List contents of a directory"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    
    try:
        data = json.loads(request.body)
        path = data.get('path')
        
        if not path or not os.path.isdir(path):
            return JsonResponse({'error': 'Invalid directory path.'}, status=400)
        
        items = []
        for entry in sorted(os.listdir(path)):
            if entry.startswith('.'):
                continue  # Skip hidden files
            
            full_path = os.path.join(path, entry)
            items.append({
                'name': entry,
                'path': full_path,
                'type': 'directory' if os.path.isdir(full_path) else 'file'
            })
        
        # Return files list for backward compatibility
        files = [item['path'] for item in items if item['type'] == 'file']
        
        return JsonResponse({'files': files, 'items': items})
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def fetch_file_api(request):
    """Fetch a file and return it as base64 for attachment"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    
    try:
        data = json.loads(request.body)
        path = data.get('path')
        
        if not path or not os.path.isfile(path):
            return JsonResponse({'error': 'Invalid file path.'}, status=400)
        
        filename = os.path.basename(path)
        
        # Read file as binary and encode to base64
        with open(path, 'rb') as f:
            content = base64.b64encode(f.read()).decode('utf-8')
        
        return JsonResponse({
            'filename': filename,
            'content': content,
            'path': path
        })
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt  
def read_file_content_api(request):
    """Read and return file content as text (for preview)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method.'}, status=405)
    
    try:
        data = json.loads(request.body)
        path = data.get('path')
        
        if not path or not os.path.isfile(path):
            return JsonResponse({'error': 'Invalid file path.'}, status=400)
        
        filename = os.path.basename(path)
        file_ext = os.path.splitext(filename)[1].lower()
        
        # Extract content based on file type
        content = ''
        if file_ext == '.pdf':
            with open(path, 'rb') as f:
                pdf_reader = PyPDF2.PdfReader(f)
                text_parts = []
                for page in pdf_reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                content = "\n".join(text_parts)
        else:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(100000)  # Read first 100KB
        
        return JsonResponse({
            'filename': filename,
            'content': content[:50000],  # Limit content for response
            'truncated': len(content) > 50000
        })
    
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
