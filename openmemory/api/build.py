#!/bin/bash

# Build script for OpenMemory MCP API
set -e  # Exit on any error

echo "🚀 Starting OpenMemory build process..."

# Navigate to API directory
cd openmemory/api || cd api || {
    echo "❌ Could not find API directory"
    exit 1
}

echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

echo "🔧 Setting up database..."
python startup.py

echo "✅ Build completed successfully!"
