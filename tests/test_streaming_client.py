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

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def format_timestamp():
    """Get current timestamp formatted."""
    return datetime.now().strftime("%H:%M:%S")


class DualLogger:
    """Logger that writes to both terminal (with colors) and file (clean)."""

    def __init__(self, filename="streaming_trace.txt"):
        self.terminal_file = sys.stdout
        self.log_file = open(filename, "w", encoding="utf-8")
        self.ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    def log(self, text: str, end: str = "\n", flush: bool = False):
        """Write text to both destinations."""
        # Terminal gets original text (with colors)
        print(text, end=end, file=self.terminal_file, flush=flush)

        # File gets clean text
        clean_text = self.ansi_escape.sub("", text)
        print(clean_text, end=end, file=self.log_file, flush=flush)

    def close(self):
        self.log_file.close()


import re


def print_progress(
    logger: DualLogger, event_type: str, status: str, message: str, details: dict = None
):
    """Print a formatted progress update."""

    # Map event types to emojis and colors
    event_info = {
        "memory_retrieval": ("🧠", Colors.CYAN, "Memory"),
        "retrieval_strategy": ("🎯", Colors.BLUE, "Strategy"),
        "document_retrieval": ("📚", Colors.GREEN, "Documents"),
        "context_evaluation": ("🔍", Colors.YELLOW, "Evaluation"),
        "web_search": ("🌐", Colors.CYAN, "Web Search"),
        "answer_generation": ("✨", Colors.GREEN, "Generating"),
    }

    emoji, color, label = event_info.get(event_type, ("📌", Colors.HEADER, event_type))

    # Status indicators
    status_symbols = {"in_progress": "⏳", "completed": "✅", "failed": "❌"}
    status_symbol = status_symbols.get(status, "•")

    # Format the output
    timestamp = format_timestamp()
    logger.log(
        f"{color}[{timestamp}] {emoji} {label:<12} {status_symbol} {message}{Colors.ENDC}"
    )

    # Print details if available
    if details:
        # Special handling for web_results to display them nicely
        if "web_results" in details:
            web_results = details.pop("web_results")
            # Print other details first
            if details:
                other_details = " | ".join([f"{k}: {v}" for k, v in details.items()])
                logger.log(f"{'':>21} └─ {other_details}")
            # Print web results
            logger.log(f"{'':>21} └─ {Colors.CYAN}Web Sources:{Colors.ENDC}")
            for i, result in enumerate(web_results, 1):
                title = result.get("title", "Untitled")
                url = result.get("url", "")
                logger.log(f"{'':>24} {i}. {title}")
                logger.log(f"{'':>27} {Colors.BLUE}{url}{Colors.ENDC}")
        else:
            # Filter out strategy and char_count if they somehow appear
            filtered_details = {
                k: v for k, v in details.items() if k not in ["strategy", "char_count"]
            }
            if filtered_details:
                detail_str = " | ".join(
                    [f"{k}: {v}" for k, v in filtered_details.items()]
                )
                logger.log(f"{'':>21} └─ {detail_str}")


def stream_agentic_rag(
    query: str,
    user_id: str = "test_user",
    session_id: str = "test_session",
    base_url: str = "http://localhost:8085",
):
    """
    Stream an agentic RAG query and display progress.

    Args:
        query: The question to ask
        user_id: User identifier
        session_id: Session identifier
        base_url: Base URL of the API
    """
    url = f"{base_url}/api/chat/agent/stream"
    payload = {"query": query, "user_id": user_id, "session_id": session_id}

    logger = DualLogger()

    logger.log(f"\n{Colors.BOLD}{'='*80}{Colors.ENDC}")
    logger.log(f"{Colors.BOLD}🚀 Starting Agentic RAG Query{Colors.ENDC}")
    logger.log(f"{Colors.BOLD}{'='*80}{Colors.ENDC}\n")
    logger.log(f"{Colors.HEADER}Query:{Colors.ENDC} {query}\n")
    logger.log(f"{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")

    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "x-api-hbai-key": "3",
    }

    answer_buffer = []

    try:
        with requests.post(
            url, json=payload, headers=headers, stream=True, timeout=120
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue

                line_str = line.decode("utf-8")

                if line_str.startswith("data: "):
                    try:
                        event = json.loads(line_str[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "progress":
                        # Check if this is actually a chunk wrapped in a progress event
                        if event.get("event_type") == "chunk":
                            chunk = event.get("message", "")
                            answer_buffer.append(chunk)

                            # Start answer section on first chunk
                            if len(answer_buffer) == 1:
                                logger.log(f"\n{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
                                logger.log(
                                    f"{Colors.BOLD}{Colors.GREEN}📝 Answer:{Colors.ENDC}\n"
                                )

                            logger.log(chunk, end="", flush=True)
                        else:
                            # Standard progress event
                            print_progress(
                                logger,
                                event.get("event_type", ""),
                                event.get("status", ""),
                                event.get("message", ""),
                                event.get("details"),
                            )

                    elif event_type == "chunk":
                        chunk = event.get("chunk", "")
                        answer_buffer.append(chunk)

                        # Start answer section on first chunk
                        if len(answer_buffer) == 1:
                            logger.log(f"\n{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
                            logger.log(
                                f"{Colors.BOLD}{Colors.GREEN}📝 Answer:{Colors.ENDC}\n"
                            )

                        logger.log(chunk, end="", flush=True)

                    elif event_type == "end":
                        logger.log(f"\n\n{Colors.BOLD}{'─'*80}{Colors.ENDC}\n")
                        logger.log(
                            f"{Colors.GREEN}✨ Stream completed successfully!{Colors.ENDC}"
                        )
                        logger.log(f"\n{Colors.BOLD}{'='*80}{Colors.ENDC}\n")
                        break

                    elif event_type == "error":
                        error_msg = event.get("error", "Unknown error")
                        logger.log(
                            f"\n{Colors.RED}❌ Error: {error_msg}{Colors.ENDC}\n"
                        )
                        break

                    elif event_type == "debug":
                        # Debug data (only in development mode)
                        debug_data = event.get("data", {})
                        logger.log(
                            f"{Colors.YELLOW}🐛 Debug: {json.dumps(debug_data, indent=2)}{Colors.ENDC}\n"
                        )

            # Dump full prompt-response trace for frontend devs
            with open("frontend_trace.json", "w", encoding="utf-8") as f:
                trace_data = {
                    "query": query,
                    "answer": "".join(answer_buffer),
                    "events": "See 'streaming_trace.txt' for event log",
                }
                json.dump(trace_data, f, indent=2, ensure_ascii=False)
                logger.log(f"\nSaved simplified trace to frontend_trace.json")
                logger.log(f"Saved detailed log to streaming_trace.txt")

    except requests.exceptions.ConnectionError:
        logger.log(
            f"\n{Colors.RED}❌ Connection Error: Could not connect to {base_url}{Colors.ENDC}"
        )
        logger.log(
            f"{Colors.YELLOW}Make sure the API server is running.{Colors.ENDC}\n"
        )
    except requests.exceptions.Timeout:
        logger.log(f"\n{Colors.RED}❌ Timeout: Request took too long{Colors.ENDC}\n")
    except requests.exceptions.RequestException as e:
        logger.log(f"\n{Colors.RED}❌ Request Error: {e}{Colors.ENDC}\n")
    except KeyboardInterrupt:
        logger.log(f"\n\n{Colors.YELLOW}⚠️  Interrupted by user{Colors.ENDC}\n")
    except Exception as e:
        logger.log(f"\n{Colors.RED}❌ Unexpected Error: {e}{Colors.ENDC}\n")
    finally:
        logger.close()


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

        choice = input(
            f"Select a query (1-{len(example_queries)}) or press Enter for custom query: "
        ).strip()

        if choice.isdigit() and 1 <= int(choice) <= len(example_queries):
            query = example_queries[int(choice) - 1]
        else:
            query = input("Enter your question: ").strip()
            if not query:
                query = example_queries[0]

    # Stream the query
    stream_agentic_rag(query)
