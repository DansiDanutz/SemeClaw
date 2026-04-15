---
name: "skill-creator"
description: "Create new skills to extend agent capabilities"
author: "DansLab"
version: "1.0.0"
tags: ["meta", "skill-creation"]
---

# Skill Creator

The skill-creator tool helps you build new skills that extend SemeClaw agent capabilities.

## What is a Skill?

A skill is a reusable capability that agents can discover and execute. Skills are defined using `SKILL.md` files and live in the `skills/` directory.

### Skill Structure

```
skills/my-skill-name/
  SKILL.md               # Skill definition (this file)
  __init__.py           # Python implementation (if code-based)
  requirements.txt      # Dependencies (if code-based)
```

## Creating a New Skill

### Step 1: Plan Your Skill

Before building, define:
- **Name**: Lowercase, kebab-case (e.g., `email-summarizer`, `csv-analyzer`)
- **Purpose**: What specific capability does it provide?
- **Input**: What parameters does it accept?
- **Output**: What does it return?
- **Use Cases**: When would an agent use this?

### Step 2: Define the SKILL.md

Create `skills/my-skill-name/SKILL.md`:

```markdown
---
name: "my-skill-name"
description: "One-line description of what this skill does"
author: "Your Name"
version: "1.0.0"
tags: ["category", "keywords"]
---

# My Skill

## Overview
Detailed description of the skill and its purpose.

## Capabilities
- Capability 1
- Capability 2

## Usage

Call this skill using the standard tool interface:

\`\`\`
tool_call("my-skill-name", parameter="value")
\`\`\`

## Parameters

- `param1` (str, required): Description
- `param2` (int, optional): Description (default: 10)

## Returns

Success: JSON object with results
Error: Error message string

## Examples

### Example 1
Input: ...
Output: ...
```

### Step 3: Implement the Skill

For code-based skills, implement in `__init__.py`:

```python
from __future__ import annotations

async def execute(params: dict) -> str:
    """Execute the skill.
    
    Args:
        params: Parameter dictionary from tool call
        
    Returns:
        Result as JSON string or error message
    """
    param1 = params.get("param1")
    
    # Implementation here
    
    return json.dumps({"success": True, "result": ...})
```

### Step 4: Test Your Skill

Test using the SemeClaw CLI:

```bash
semeclaw test-skill skills/my-skill-name
```

### Step 5: Document Examples

Update `SKILL.md` with real working examples of the skill in action.

## Best Practices

### Naming
- Use kebab-case for skill names
- Names should indicate the primary capability
- Avoid generic names like "helper" or "tool"

### Documentation
- Include clear parameter descriptions
- Provide example inputs and outputs
- Explain when to use this skill vs alternatives

### Error Handling
- Return JSON with `{"success": false, "error": "message"}` on failure
- Provide actionable error messages
- Log unexpected conditions

### Performance
- Skills should complete in < 5 seconds
- For long operations, dispatch to background agents
- Consider using post_message() for asynchronous results

## Skill Categories

- **data**: Data processing, analysis, transformation
- **integration**: External service integration, APIs
- **analysis**: Research, investigation, learning
- **generation**: Content creation, writing, synthesis
- **media**: Image, audio, video processing
- **meta**: Tools about tools (like this one)

## Examples in This Workspace

Review existing skills in the `skills/` directory for implementation patterns:

- `skill-creator` - This skill (create and modify skills)
- Other domain-specific skills

## Troubleshooting

### Skill Not Found
- Check the skill name matches exactly (case-sensitive)
- Verify `SKILL.md` exists and is valid

### Parameters Not Working
- Ensure parameter names in tool_call match SKILL.md
- Check required vs optional parameters
- Validate parameter types

### Integration Issues
- Check dependencies in `requirements.txt`
- Ensure external services are accessible
- Add error handling for network failures

## Version History

### 1.0.0 (Current)
- Initial release
- Basic skill creation workflow
- Support for code and definition-based skills