# Provider Example Configuration

### Anthropic (Recommended for SemeClaw)

```yaml
llm:
    provider: anthropic
    model: claude-sonnet-4-20250514
    api_key: your-anthropic-api-key
```
Get your API key at [Anthropic Console](https://console.anthropic.com).

### OpenAI

```yaml
llm:
    provider: openai
    model: gpt-4
    api_key: sk-your-openai-api-key
```

### Google (Gemini)

```yaml
llm:
    provider: google
    model: gemini-2.0-flash
    api_key: your-google-api-key
```
Get your API key at [Google AI Studio](https://aistudio.google.com/app/apikey).

### Grok (OpenAI-compatible)

```yaml
llm:
    provider: openai
    model: grok-2-1212
    api_key: your-x-api-key
    api_base: https://api.x.ai/v1
    temperature: 0.7
```
Get your API key at [x.ai](https://console.x.ai).
