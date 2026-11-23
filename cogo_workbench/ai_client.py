"""Local Ollama and OpenAI-compatible chat. Standard library only.

xAI (Grok) is https://api.x.ai/v1, model grok-4.7, key from XAI_API_KEY or a
QGIS authentication config. The key is never written into this plugin.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_OLLAMA = "http://localhost:11434"
XAI_URL = "https://api.x.ai/v1"
XAI_MODEL = "grok-4.7"
PROVIDERS = {
    "ollama": ("Ollama (local)", DEFAULT_OLLAMA),
    "xai": ("xAI (Grok)", XAI_URL),
    "custom": ("Custom (OpenAI-compatible)", ""),
}


class AIError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise AIError("AI service redirects are disabled to protect deed contents and credentials.")


def _open(req, timeout):
    # No ambient proxy: a proxy would see the deed and the API key.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout)


def _loopback(base, path):
    try:
        parts = urllib.parse.urlsplit((base or DEFAULT_OLLAMA).strip())
        if (parts.scheme not in ("http", "https") or parts.hostname not in ("localhost", "127.0.0.1", "::1")
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment
                or parts.path not in ("", "/")):
            raise ValueError()
        port = parts.port
    except ValueError:
        raise AIError("Ollama must be on this computer: http://localhost:11434 or a loopback address.") from None
    host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
    return "%s://%s%s%s" % (parts.scheme, host, ":%d" % port if port is not None else "", path)


def endpoint(base):
    try:
        parts = urllib.parse.urlsplit(base.strip().rstrip("/"))
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment):
            raise ValueError()
        parts.port
        if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise AIError("Use HTTPS for an AI service that is not on this computer.")
        if parts.hostname == "localhost":
            parts = parts._replace(netloc="127.0.0.1" + (":%d" % parts.port if parts.port else ""))
        return urllib.parse.urlunsplit(parts)
    except ValueError:
        raise AIError("Enter an HTTP(S) API base URL without a key, query or fragment.") from None


def api_key(authcfg):
    """QGIS authentication password, or XAI_API_KEY. Never saved by this plugin."""
    if authcfg:
        from qgis.core import QgsApplication, QgsAuthMethodConfig
        config = QgsAuthMethodConfig()
        manager = QgsApplication.authManager()
        if manager is None or not manager.loadAuthenticationConfig(authcfg, config, True):
            raise AIError("Unlock QGIS authentication, or select the config that holds the API key.")
        if config.method() != "Basic" or not config.config("password"):
            raise AIError("Use a Basic authentication config with the API key in its password field.")
        return config.config("password")
    key = os.environ.get("XAI_API_KEY", "").strip()
    if key:
        return key
    raise AIError("Set XAI_API_KEY, or choose a QGIS authentication config whose password is the API key.")


def pick(models, *prefixes):
    for prefix in prefixes:
        for name in models:
            if name.split(":")[0] == prefix or name.startswith(prefix):
                return name
    return models[0] if models else ""


class Client:
    def __init__(self, provider, url, key=""):
        if provider not in PROVIDERS:
            raise AIError("Choose an AI service.")
        self.provider, self.url, self.key = provider, url, key
        if provider == "ollama":
            _loopback(url, "/api/tags")
        else:
            endpoint(url)
            if not key:
                raise AIError("This service needs an API key (XAI_API_KEY or a QGIS authentication config).")

    def _request(self, path, body=None, timeout=600):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        data = json.dumps(body).encode("utf-8") if body is not None else None
        base = _loopback(self.url, path) if self.provider == "ollama" else endpoint(self.url) + path
        req = urllib.request.Request(base, data=data, headers=headers)
        try:
            with _open(req, timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error.close()
            raise AIError("AI service returned HTTP %d. Check the address, key and model." % error.code) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise AIError("Could not read the AI service. Check that it is running and the address is right.") from None

    def list_models(self):
        if self.provider == "ollama":
            data = self._request("/api/tags", timeout=5)
            return sorted(item.get("name", "") for item in data.get("models", []) if item.get("name"))
        data = self._request("/models", timeout=10)
        try:
            return sorted(item["id"] for item in data["data"] if isinstance(item.get("id"), str))
        except (KeyError, TypeError, AttributeError):
            raise AIError("The service did not return a model list.") from None

    def chat(self, model, prompt, images_b64=None, schema=None, timeout=600):
        model = (model or "").strip()
        if not model:
            raise AIError("Choose a model.")
        if self.provider == "ollama":
            if "cloud" in model.lower().rsplit(":", 1)[-1].split("-"):
                raise AIError("Choose a model downloaded on this computer, not an Ollama cloud model.")
            message = {"role": "user", "content": prompt}
            if images_b64:
                message["images"] = list(images_b64)
            body = {"model": model, "messages": [message], "stream": False, "options": {"temperature": 0}}
            if schema is not None:
                body["format"] = schema
            data = self._request("/api/chat", body, timeout)
            try:
                return data["message"]["content"]
            except (KeyError, TypeError):
                raise AIError("Ollama returned no text.") from None
        # OpenAI chat-completions image shape, which api.x.ai documents for Grok.
        content = prompt
        if images_b64:
            content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + image, "detail": "high"}}
                       for image in images_b64]
            content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]
        if schema is not None:
            messages.insert(0, {"role": "system", "content": "Return only JSON matching this schema: "
                             + json.dumps(schema)})
        data = self._request("/chat/completions", {"model": model, "messages": messages, "stream": False}, timeout)
        try:
            text = data["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
            return text
        except (KeyError, IndexError, TypeError, ValueError):
            raise AIError("The model returned no text. Use a chat model that can read images.") from None

    def chat_json(self, model, prompt, schema):
        text = self.chat(model, prompt, schema=schema).strip()
        if text.startswith("```") and text.endswith("```"):
            text = text[3:-3].strip()
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        try:
            result = json.loads(text)
        except ValueError:
            raise AIError("The model did not return JSON.") from None
        if not isinstance(result, dict):
            raise AIError("The model did not return a JSON object.")
        calls = result.get("calls")
        if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
            raise AIError("The model did not return a list of calls.")
        for call in calls:
            if (any(not isinstance(call.get(key), str) for key in ("quote", "kind", "bearing"))
                    or call["kind"] not in ("line", "curve")):
                raise AIError("The model returned a call with missing quote, kind or bearing.")
        return result
