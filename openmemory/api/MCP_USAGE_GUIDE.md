# MCP Memory Server Usage Guide

## How Memory Extraction Works

The MCP Memory Server uses AI to extract facts from natural language. It **does not** store arbitrary text or commands.

### ✅ What Works (Extracts Memories)

These phrases contain extractable facts:
- "My name is John and I work at OpenAI"
- "I prefer Python programming and dark themes"
- "My email is john@example.com"
- "Remember that I live in San Francisco"
- "I have a meeting at 3pm tomorrow"

### ❌ What Doesn't Work (No Facts to Extract)

These phrases contain no extractable information:
- "add memory test" - This is just a command, no facts
- "test" - Single word, no information
- "hello" - Greeting, no facts
- "remember this" - No actual content to remember

### Tips for Users

1. **Provide actual information** - The system extracts facts, not commands
2. **Use natural language** - Write as if you're telling someone about yourself
3. **Be specific** - "I like pizza" works better than "food preference"

### Example Usage

Instead of:
```
add memory test
```

Try:
```
My favorite programming language is Python and I use VSCode
```

## Technical Details

- Facts are extracted using GPT-4
- Memories are stored in both Qdrant (vector search) and PostgreSQL (metadata)
- Simple phrases without factual content will correctly return empty results