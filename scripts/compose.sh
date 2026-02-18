#!/bin/bash

# Docker Compose Helper Script
# Usage: ./scripts/compose.sh [command]

set -e

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Navigate to the project root (parent of scripts/)
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Change to project root directory
cd "$PROJECT_ROOT"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_usage() {
    echo -e "${BLUE}Docker Compose Helper Commands:${NC}"
    echo ""
    echo -e "${GREEN}Development:${NC}"
    echo "  ./scripts/compose.sh dev-up         - Start dev containers (build + detached)"
    echo "  ./scripts/compose.sh dev-logs       - Show dev container logs (follow)"
    echo "  ./scripts/compose.sh dev-down       - Stop dev containers"
    echo ""
    echo -e "${GREEN}Production:${NC}"
    echo "  ./scripts/compose.sh up             - Start prod containers (no build, detached)"
    echo "  ./scripts/compose.sh up-build       - Start prod containers (build + detached)"
    echo "  ./scripts/compose.sh logs           - Show prod container logs (follow)"
    echo "  ./scripts/compose.sh down           - Stop prod containers"
    echo ""
}

case "$1" in
    # Development commands
    dev-up)
        echo -e "${GREEN}Starting development containers...${NC}"
        docker compose -f docker-compose.dev.yml up --build -d
        ;;
    dev-logs)
        echo -e "${GREEN}Showing development logs...${NC}"
        docker compose -f docker-compose.dev.yml logs -f
        ;;
    dev-down)
        echo -e "${GREEN}Stopping development containers...${NC}"
        docker compose -f docker-compose.dev.yml down
        ;;

    # Production commands
    up)
        echo -e "${GREEN}Starting production containers (no build)...${NC}"
        docker compose up -d
        ;;
    up-build)
        echo -e "${GREEN}Starting production containers (with build)...${NC}"
        docker compose up --build -d
        ;;
    logs)
        echo -e "${GREEN}Showing production logs...${NC}"
        docker compose logs -f
        ;;
    down)
        echo -e "${GREEN}Stopping production containers...${NC}"
        docker compose down
        ;;

    # Help
    help|--help|-h|"")
        print_usage
        ;;

    *)
        echo -e "${BLUE}Unknown command: $1${NC}"
        echo ""
        print_usage
        exit 1
        ;;
esac
