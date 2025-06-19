#!/bin/bash

# Build script for OpenMemory MCP API
set -e  # Exit on any error

echo "🚀 Starting OpenMemory build process..."

# Navigate to API directory


echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

echo "🔧 Setting up database..."
python startup.py

echo "✅ Build completed successfully!"
