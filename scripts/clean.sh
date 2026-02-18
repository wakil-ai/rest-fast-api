#!/bin/bash

# clean.sh - Python Code Cleaner (Black, Ruff, isort)
# 
# Usage:
#   ./clean.sh              # Clean current directory
#   ./clean.sh src/         # Clean specific directory
#   ./clean.sh myfile.py    # Clean specific file
#   ./clean.sh --check      # Check only, don't fix
#   ./clean.sh --install    # Install required tools

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Emojis
CHECK="✅"
CROSS="❌"
TOOLS="🔧"
CLEAN="🧹"
WARN="⚠️"
SPARK="✨"

# Default values
TARGET="."
CHECK_ONLY=false

# Function to print colored messages
print_header() {
    echo ""
    echo -e "${BLUE}============================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}============================================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}${CHECK} $1${NC}"
}

print_error() {
    echo -e "${RED}${CROSS} $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}${WARN} $1${NC}"
}

print_info() {
    echo -e "${BLUE}${TOOLS} $1${NC}"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to install tools
install_tools() {
    print_header "${CLEAN} Installing Black, Ruff, and isort"
    
    if command_exists pip3; then
        PIP="pip3"
    elif command_exists pip; then
        PIP="pip"
    else
        print_error "pip not found. Please install Python and pip first."
        exit 1
    fi
    
    print_info "Installing with $PIP..."
    $PIP install black ruff isort
    
    print_success "Installation complete!"
    echo ""
}

# Function to run a command and check result
run_command() {
    local cmd="$1"
    local description="$2"
    
    print_info "$description..."
    
    if eval "$cmd" 2>&1; then
        print_success "$description - Done"
        return 0
    else
        print_warning "$description - Issues found"
        return 1
    fi
}

# Function to clean code
clean_code() {
    local target="$1"
    local check_only="$2"
    
    print_header "${CLEAN} Cleaning Python code: $target"
    
    local success=true
    
    # Check if tools are installed
    if ! command_exists isort; then
        print_error "isort not found. Install with: pip install isort"
        print_info "Or run: $0 --install"
        return 1
    fi
    
    if ! command_exists black; then
        print_error "black not found. Install with: pip install black"
        print_info "Or run: $0 --install"
        return 1
    fi
    
    if ! command_exists ruff; then
        print_error "ruff not found. Install with: pip install ruff"
        print_info "Or run: $0 --install"
        return 1
    fi
    
    # 1. isort - Sort imports
    if [ "$check_only" = true ]; then
        run_command "isort --check-only --profile black '$target'" "Sorting imports (isort)" || success=false
    else
        run_command "isort --profile black '$target'" "Sorting imports (isort)" || success=false
    fi
    
    # 2. Black - Format code
    if [ "$check_only" = true ]; then
        run_command "black --check '$target'" "Formatting code (Black)" || success=false
    else
        run_command "black '$target'" "Formatting code (Black)" || success=false
    fi
    
    # 3. Ruff - Lint and fix
    if [ "$check_only" = true ]; then
        run_command "ruff check '$target'" "Linting code (Ruff)" || success=false
    else
        run_command "ruff check --fix '$target'" "Linting code (Ruff)" || success=false
    fi
    
    # Summary
    print_header "Summary"
    if [ "$success" = true ]; then
        print_success "All done! Code is clean. ${SPARK}"
    else
        print_warning "Some issues remain. Check output above."
    fi
    echo ""
    
    return 0
}

# Function to show help
show_help() {
    cat << EOF
Python Code Cleaner - Black, Ruff, isort

Usage:
    $0 [OPTIONS] [TARGET]

Arguments:
    TARGET          File or directory to clean (default: current directory)

Options:
    --check         Check only, don't modify files
    --install       Install Black, Ruff, and isort
    -h, --help      Show this help message

Examples:
    $0                      # Clean current directory
    $0 src/                 # Clean src directory
    $0 myfile.py            # Clean specific file
    $0 --check              # Check without modifying
    $0 --install            # Install tools

Tools used:
    isort               Sort and organize imports
    Black               Format code to consistent style
    Ruff                Lint code and auto-fix issues

EOF
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --check)
            CHECK_ONLY=true
            shift
            ;;
        --install)
            install_tools
            exit 0
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            TARGET="$1"
            shift
            ;;
    esac
done

# Validate target exists
if [ ! -e "$TARGET" ]; then
    print_error "Error: $TARGET not found"
    exit 1
fi

# Clean the code
clean_code "$TARGET" "$CHECK_ONLY"