"""Small provider contract: build_request(context, images) and decode_response(body)."""
import base64
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from urllib.parse import urlsplit
from .protocol import SCHEMA, SYSTEM_PROMPT, parse_decision, ProtocolError

class ProviderError(RuntimeError): pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("The API redirected the request. Check the endpoint in Connection.")

@dataclass
class Connection:
    provider: str = "OpenAI"
    model: str = "gpt-5.4"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"

def validate_base_url(url):
    p = urlsplit(url)
    if not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ProviderError("Use a plain API base URL without credentials, query or fragment.")
    if p.scheme != "https" and not (p.scheme == "http" and p.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise ProviderError("Remote API connections require HTTPS. HTTP is allowed on localhost only.")
    return url.rstrip("/")

class OpenAIProvider:
    default_url = "https://api.openai.com/v1"
    default_model = "gpt-5.4"
    env_key = "OPENAI_API_KEY"

    def build_request(self, c, context, images):
        content = [{"type": "input_text", "text": context}]
        for label, data in images:
            content.extend([{"type": "input_text", "text": label}, {"type": "input_image", "detail": "high",
                "image_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}])
        body = {"model": c.model, "store": False, "instructions": SYSTEM_PROMPT,
                "input": [{"role": "user", "content": content}], "max_output_tokens": 3000,
                "tools": [{"type": "function", "name": "submit_decision", "description": "Propose one visible editor action or report completion.",
                           "parameters": SCHEMA, "strict": True}],
                "tool_choice": {"type": "function", "name": "submit_decision"}, "parallel_tool_calls": False}
        return self.default_url + "/responses", {"Authorization": "Bearer " + c.api_key}, body

    def decode_response(self, body):
        calls = [x for x in body.get("output", []) if x.get("type") == "function_call" and x.get("name") == "submit_decision"]
        if len(calls) != 1: raise ProviderError("OpenAI returned no single editor decision. Check model/tool support or retry.")
        return parse_decision(json.loads(calls[0]["arguments"]))

class ClaudeProvider:
    default_url = "https://api.anthropic.com/v1"
    default_model = "claude-sonnet-4-6"
    env_key = "ANTHROPIC_API_KEY"

    def build_request(self, c, context, images):
        content = [{"type": "text", "text": context}]
        for label, data in images:
            content.extend([{"type": "text", "text": label}, {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": base64.b64encode(data).decode("ascii")}}])
        body = {"model": c.model, "max_tokens": 2500, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": content}],
                "tools": [{"name": "submit_decision", "description": "Propose one visible editor action or report completion.", "input_schema": SCHEMA}],
                "tool_choice": {"type": "tool", "name": "submit_decision", "disable_parallel_tool_use": True}}
        return self.default_url + "/messages", {"x-api-key": c.api_key, "anthropic-version": "2023-06-01"}, body

    def decode_response(self, body):
        calls = [x for x in body.get("content", []) if x.get("type") == "tool_use" and x.get("name") == "submit_decision"]
        if len(calls) != 1: raise ProviderError("Claude returned no single editor decision. Check model/tool support or retry.")
        return parse_decision(calls[0]["input"])

class CompatibleProvider:
    default_url = "http://localhost:11434/v1"
    default_model = ""
    env_key = "LLM_API_KEY"

    def build_request(self, c, context, images):
        content = [{"type": "text", "text": context}]
        for label, data in images:
            content.extend([{"type": "text", "text": label}, {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")}}])
        body = {"model": c.model, "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
                "tools": [{"type": "function", "function": {"name": "submit_decision", "description": "Propose one editor action or report completion.", "parameters": SCHEMA}}],
                "tool_choice": {"type": "function", "function": {"name": "submit_decision"}}}
        return validate_base_url(c.base_url) + "/chat/completions", ({"Authorization": "Bearer " + c.api_key} if c.api_key else {}), body

    def decode_response(self, body):
        choices = body.get("choices", [])
        calls = choices[0].get("message", {}).get("tool_calls", []) if choices else []
        if len(calls) != 1 or calls[0].get("function", {}).get("name") != "submit_decision":
            raise ProviderError("This endpoint must support image inputs and function calling.")
        return parse_decision(json.loads(calls[0]["function"]["arguments"]))

PROVIDERS = {"OpenAI": OpenAIProvider, "Claude": ClaudeProvider, "OpenAI-compatible": CompatibleProvider}

def request_decision(connection, context, images):
    if connection.provider not in PROVIDERS: raise ProviderError("Unknown provider.")
    if not connection.model.strip(): raise ProviderError("Enter a vision-capable model ID in Connection.")
    if connection.provider != "OpenAI-compatible" and not connection.api_key.strip():
        raise ProviderError("Add your API key in Connection first.")
    provider = PROVIDERS[connection.provider]()
    url, headers, body = provider.build_request(connection, context, images)
    req = urllib.request.Request(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as response:
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000: raise ProviderError("API response was too large.")
            result = json.loads(payload)
        return provider.decode_response(result)
    except urllib.error.HTTPError as e:
        # Do not echo response bodies: they can contain keys or submitted design data.
        hint = {401: "API key was rejected.", 403: "This account cannot use the requested model.",
                404: "Check the model ID and endpoint.", 429: "Rate limit or quota reached; wait or check billing.",
                400: "Check model support for images and tools, and the model ID."}.get(e.code, "Provider request failed; retry later.")
        raise ProviderError(f"HTTP {e.code}: {hint}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError("Could not reach the provider within 60 seconds. Check your connection and endpoint.") from None
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, ProtocolError): raise
        raise ProviderError("The provider returned an unreadable decision.") from None
