"""
Example client for testing Agentic RAG progress streaming.

This script demonstrates how to consume the streaming endpoint
and display progress updates in real-time.
"""

import requests
import json
import sys
from datetime import datetime


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def format_timestamp():
    """Get current timestamp formatted."""
    return datetime.now().strftime("%H:%M:%S")


def print_progress(event_type: str, status: str, message: str, details: dict = None):
    """Print a formatted progress update."""
    
    # Map event types to emojis and colors
    event_info = {
        'memory_retrieval': ('🧠', Colors.CYAN, 'Memory'),
        'retrieval_strategy': ('🎯', Colors.BLUE, 'Strategy'),
        'document_retrieval': ('📚', Colors.GREEN, 'Documents'),
        'context_evaluation': ('🔍', Colors.YELLOW, 'Evaluation'),
        'web_search': ('🌐', Colors.CYAN, 'Web Search'),
        'answer_generation': ('✨', Colors.GREEN, 'Generating'),
    }
    
    emoji, color, label = event_info.get(event_type, ('📌', Colors.HEADER, event_type))
    
    # Status indicators
    status_symbols = {
        'in_progress': '⏳',
        'completed': '✅',
        'failed': '❌'
    }
    status_symbol = status_symbols.get(status, '•')
    
    # Format the output
    timestamp = format_timestamp()
    print(f"{color}[{timestamp}] {emoji} {label:<12} {status_symbol} {message}{Colors.ENDC}")
    
    # Print details if available
    if details:
        # Special handling for web_results to display them nicely
        if 'web_results' in details:
            web_results = details.pop('web_results')
            # Print other details first
            if details:
                other_details = " | ".join([f"{k}: {v}" for k, v in details.items()])
                print(f"{'':>21} └─ {other_details}")
            # Print web results
            print(f"{'':>21} └─ {Colors.CYAN}Web Sources:{Colors.ENDC}")
            for i, result in enumerate(web_results, 1):
                title = result.get('title', 'Untitled')
                url = result.get('url', '')
                print(f"{'':>24} {i}. {title}")
                print(f"{'':>27} {Colors.BLUE}{url}{Colors.ENDC}")
        else:
            # Filter out strategy and char_count if they somehow appear
            filtered_details = {k: v for k, v in details.items() if k not in ['strategy', 'char_count']}
            if filtered_details:
                detail_str = " | ".join([f"{k}: {v}" for k, v in filtered_details.items()])
                print(f"{'':>21} └─ {detail_str}")


def stream_agentic_rag(query: str, user_id: str = "test_user", session_id: str = "test_session", base_url: str = "http://localhost:8085"):
    """
    Stream an agentic RAG query and display progress.
    
    Args:
        query: The question to ask
        user_id: User identifier
        session_id: Session identifier
        base_url: Base URL of the API
    """
    url = f"{base_url}/api/chat/agent/stream"
    payload = {
        "query": query,
        "user_id": user_id,
        "session_id": session_id
    }
    
    print(f"\n{Colors.BOLD}{'='*80}{Colors.ENDC}")
    print(f"{Colors.BOLD}🚀 Starting Agentic RAG Query{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*80}{Colors.ENDC}\n")
    print(f"{Colors.HEADER}Query:{Colors.ENDC} {query}\n")
    print(f"{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
    
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "x-api-hbai-key": "3"
    }
    
    answer_buffer = []
    
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=120) as response:
            response.raise_for_status()
            
            for line in response.iter_lines():
                if not line:
                    continue
                
                line_str = line.decode('utf-8')
                
                if line_str.startswith('data: '):
                    try:
                        event = json.loads(line_str[6:])
                    except json.JSONDecodeError:
                        continue
                    
                    event_type = event.get('type')
                    
                    if event_type == 'progress':
                        # Debug: print raw details
                        print_progress(
                            event.get('event_type', ''),
                            event.get('status', ''),
                            event.get('message', ''),
                            event.get('details')
                        )
                    
                    elif event_type == 'chunk':
                        chunk = event.get('chunk', '')
                        answer_buffer.append(chunk)
                        
                        # Start answer section on first chunk
                        if len(answer_buffer) == 1:
                            print(f"\n{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
                            print(f"{Colors.BOLD}{Colors.GREEN}📝 Answer:{Colors.ENDC}\n")
                        
                        print(chunk, end='', flush=True)
                    
                    elif event_type == 'end':
                        print(f"\n\n{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
                        print(f"{Colors.GREEN}✨ Stream completed successfully!{Colors.ENDC}")
                        print(f"\n{Colors.BOLD}{'='*80}{Colors.ENDC}\n")
                        break
                    
                    elif event_type == 'error':
                        error_msg = event.get('error', 'Unknown error')
                        print(f"\n{Colors.RED}❌ Error: {error_msg}{Colors.ENDC}\n")
                        break
                    
                    elif event_type == 'debug':
                        # Debug data (only in development mode)
                        debug_data = event.get('data', {})
                        print(f"{Colors.YELLOW}🐛 Debug: {json.dumps(debug_data, indent=2)}{Colors.ENDC}\n")
    
    except requests.exceptions.ConnectionError:
        print(f"\n{Colors.RED}❌ Connection Error: Could not connect to {base_url}{Colors.ENDC}")
        print(f"{Colors.YELLOW}Make sure the API server is running.{Colors.ENDC}\n")
    except requests.exceptions.Timeout:
        print(f"\n{Colors.RED}❌ Timeout: Request took too long{Colors.ENDC}\n")
    except requests.exceptions.RequestException as e:
        print(f"\n{Colors.RED}❌ Request Error: {e}{Colors.ENDC}\n")
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}⚠️  Interrupted by user{Colors.ENDC}\n")
    except Exception as e:
        print(f"\n{Colors.RED}❌ Unexpected Error: {e}{Colors.ENDC}\n")


if __name__ == "__main__":
    # Example queries
    example_queries = [
        "What are the tax regulations for freelancers in Uzbekistan?",
        "Explain the labor code provisions for employment contracts",
        "What are the penalties for late tax payment?",
    ]
    
    # Get query from command line or use default
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        print(f"{Colors.BOLD}Example Queries:{Colors.ENDC}")
        for i, q in enumerate(example_queries, 1):
            print(f"  {i}. {q}")
        print()
        
        choice = input(f"Select a query (1-{len(example_queries)}) or press Enter for custom query: ").strip()
        
        if choice.isdigit() and 1 <= int(choice) <= len(example_queries):
            query = example_queries[int(choice) - 1]
        else:
            query = input("Enter your question: ").strip()
            if not query:
                query = example_queries[0]
    
    # Stream the query
    stream_agentic_rag(query)
