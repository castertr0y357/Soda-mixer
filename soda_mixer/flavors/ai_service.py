import requests
import json
from .models import LLMProvider, SystemConfiguration

class AIAssistant:
    """Service for interacting with various LLM providers."""

    SYSTEM_PROMPT = """
    You are the Lead Creative Mixologist at the "Beverage Laboratory," a high-end, 
    scientific-themed soda and coffee mixing facility. Your goal is to assist users 
    in synthesizing perfect liquid compounds.

    Personality:
    - You are enthusiastic about flavor science.
    - You use laboratory terminology (synthesis, compound, reagent, base, stabilizer).
    - You are a "Creative Mixologist"—you value bold, experimental pairings over 
      safe bets, but you always anchor them in flavor balance.
    - You understand Sweetness, Acidity, Bitterness, and Intensity as the core axes 
      of a drink.

    Context:
    - You have access to a user's current inventory and their high-rated recipes.
    - Users will send you their "Current Compound" (selected ingredients).
    - You should suggest a 3rd or 4th ingredient to "Bridge" or "Stabilize" the mix.
    - Explain the flavor science: why does the acidity of Lemon balance the bitterness of Espresso?

    Guidelines:
    - Keep responses concise (2-3 short paragraphs).
    - Suggest specific ml/g or "parts" ratios.
    - Be supportive of "Experimental Mode" requests.
    """

    @classmethod
    def get_default_provider(cls):
        config = SystemConfiguration.get_config()
        if config.default_llm_provider and config.default_llm_provider.is_enabled:
            return config.default_llm_provider
        
        # Fallback to the first enabled provider if default is missing
        return LLMProvider.objects.filter(is_enabled=True).first()

    @classmethod
    def chat(cls, user_prompt, history=None, provider=None, context=None):
        """
        Send a prompt to the configured LLM provider.
        history: List of previous messages for context.
        context: Optional additional context (e.g. inventory registry).
        """
        if not provider:
            provider = cls.get_default_provider()
        
        if not provider:
            return "Error: No AI Laboratory Assistant is configured or enabled. Please check settings."

        system_content = cls.SYSTEM_PROMPT
        if context:
            system_content += f"\n\nUSER'S LABORATORY INVENTORY REGISTRY:\n{context}"

        messages = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_prompt})

        try:
            if provider.provider_type == 'OPENAI':
                return cls._call_openai(provider, messages)
            elif provider.provider_type == 'CLAUDE':
                return cls._call_claude(provider, messages)
            elif provider.provider_type == 'GEMINI':
                return cls._call_gemini(provider, messages)
            elif provider.provider_type == 'OLLAMA':
                return cls._call_ollama(provider, messages)
            else:
                # Generic OpenAI-compatible
                return cls._call_openai(provider, messages)
        except Exception as e:
            return f"Laboratory Error: Failed to reach the assistant ({str(e)})."

    @classmethod
    def keep_warm(cls):
        """
        Send a lightweight keep-alive pulse to local models to keep them in VRAM.
        Uses Ollama's /api/show endpoint — returns model metadata instantly with
        zero token generation, so it never blocks the Ollama request queue.
        """
        provider = cls.get_default_provider()
        if not provider or provider.provider_type not in ['OLLAMA', 'CUSTOM', 'ANYTHINGLLM']:
            return False

        try:
            if provider.provider_type == 'OLLAMA':
                base = (provider.base_url or "http://localhost:11434").rstrip('/')
                model = provider.default_model or "mistral"
                # /api/show returns model metadata instantly — no generation queued,
                # no blocking, and it resets Ollama's internal idle timer.
                response = requests.post(
                    f"{base}/api/show",
                    json={"name": model},
                    timeout=10
                )
                return response.status_code == 200
            else:
                # For custom/AnythingLLM, minimal 1-token chat call
                cls.chat("ping", history=[], provider=provider)
                return True
        except Exception:
            return False

    @classmethod
    def check_status(cls):
        """
        Actively check if the configured AI provider is reachable and responsive.
        Returns: 'synchronized', 'dormant', or 'no_provider'
        """
        provider = cls.get_default_provider()
        if not provider:
            return 'no_provider'

        try:
            if provider.provider_type == 'OLLAMA':
                base = (provider.base_url or "http://localhost:11434").rstrip('/')
                model = provider.default_model or "mistral"
                r = requests.post(f"{base}/api/show", json={"name": model}, timeout=10)
                if r.status_code == 200:
                    # Also keep warm while we're at it
                    cls.keep_warm()
                    return 'synchronized'
                return 'dormant'
            elif provider.provider_type in ['OPENAI', 'CLAUDE', 'GEMINI', 'CUSTOM', 'ANYTHINGLLM']:
                # For cloud providers, attempt a lightweight model list call to verify the API key works
                models = cls.list_models(provider)
                return 'synchronized' if models else 'dormant'
            else:
                return 'dormant'
        except Exception:
            return 'dormant'

    @classmethod
    def list_models(cls, provider):
        """Fetch available models from the provider's API."""
        try:
            if provider.provider_type in ['OPENAI', 'CLAUDE', 'CUSTOM', 'ANYTHINGLLM']:
                return cls._list_openai_models(provider)
            elif provider.provider_type == 'OLLAMA':
                return cls._list_ollama_models(provider)
            elif provider.provider_type == 'GEMINI':
                return cls._list_gemini_models(provider)
            else:
                return []
        except Exception as e:
            print(f"Error fetching models: {e}")
            return []

    @classmethod
    def suggest_autonomous(cls, ingredients, mode='standard', inventory=None, exclude=None, retry_note=None):
        """
        Generate multiple proactive suggestions as a structured JSON array.
        Returns 3 specific ingredient recommendations from the inventory.
        """
        tone = "safe and balanced" if mode == 'standard' else "bold and experimental"
        exclude_context = f" Exclude these previously suggested items: {', '.join(exclude)}." if exclude else ""
        retry_context = f"\n\nRETRY NOTE: {retry_note}\n" if retry_note else ""
        
        prompt = f"""STRUCTURED DATA REQUEST — DO NOT RESPOND WITH PROSE.{retry_context}

Current Compound: {', '.join(ingredients)}
Mode: {tone}{exclude_context}

Task: Identify EXACTLY 3 ingredients from the Inventory Registry that pair well with the current compound.

Rules:
- ONLY use ingredients present in the Inventory Registry below.
- Each item needs a short reason (max 8 words) grounded in flavor science.
- Output MUST be a raw JSON array. No markdown, no backticks, no explanation before or after.

Required output format (copy this structure exactly):
[{{"name": "Ingredient Name", "reason": "Flavor science reason"}}, ...]"""
        return cls.chat(prompt, context=inventory)

    @classmethod
    def synthesize_flavor_summary(cls, ingredients, drink_type='SODA'):
        """
        Given a finalized set of selected ingredients, produce a brief
        synthesis report: why they work together and what to expect. Plain text, no JSON.
        """
        drink_label = {'SODA': 'soda', 'COFFEE': 'coffee drink', 'SLUSHIE': 'slushie'}.get(drink_type, 'drink')
        ingredient_list = ', '.join(f"{i['name']} (Intensity {i.get('intensity', '?')}/5)" for i in ingredients)
        
        prompt = f"""FLAVOR SYNTHESIS REPORT

Finalized {drink_label} compound: {ingredient_list}

Write a concise 2-paragraph lab report:
Paragraph 1 — FLAVOR SYNERGY: Why do these ingredients work together? Reference specific flavor science (acidity, sweetness, bitterness, intensity balance, complementary/contrasting notes).
Paragraph 2 — EXPECTED TASTE: What will this drink taste like? Describe the opening, body, and finish. Keep it vivid and specific.

Do NOT give preparation instructions. Do NOT suggest more ingredients. No markdown formatting."""
        return cls.chat(prompt)

    @classmethod
    def analyze_flavor_profile(cls, name, description):
        """Analyze a flavor and return its chemical profile as JSON."""
        prompt = f"""
        Analyze this ingredient:
        Name: {name}
        Description: {description}

        Return ONLY a JSON object with values from 1.0 to 5.0 for these metrics:
        {{
            "intensity": float,
            "sweetness": float,
            "acidity": float,
            "bitterness": float
        }}
        Base your analysis on chemical flavor profiles.
        """
        response = cls.chat(prompt)
        # Attempt to extract JSON if the LLM added filler
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start != -1 and end != -1:
                return json.loads(response[start:end])
        except:
            return None
        return None

    @staticmethod
    def _list_openai_models(provider):
        url = (provider.base_url or "https://api.openai.com/v1").rstrip('/') + "/models"
        headers = {"Authorization": f"Bearer {provider.api_key}"} if provider.api_key else {}
        if provider.provider_type == 'CLAUDE':
            headers = {
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01"
            }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return [m['id'] for m in data.get('data', [])]

    @staticmethod
    def _list_ollama_models(provider):
        url = (provider.base_url or "http://localhost:11434").rstrip('/') + "/api/tags"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return [m['name'] for m in data.get('models', [])]

    @staticmethod
    def _list_gemini_models(provider):
        api_key = provider.api_key
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        # Filter for models that support generateContent
        return [m['name'].replace('models/', '') for m in data.get('models', []) 
                if 'generateContent' in m.get('supportedGenerationMethods', [])]

    @staticmethod
    def _call_openai(provider, messages):
        url = provider.base_url or "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": provider.default_model or "gpt-3.5-turbo",
            "messages": messages,
            "temperature": 0.7
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']

    @staticmethod
    def _call_ollama(provider, messages):
        # Ollama /api/chat — native format.
        url = (provider.base_url or "http://localhost:11434").rstrip('/') + "/api/chat"
        data = {
            "model": provider.default_model or "mistral",
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": 512  # Cap generation to prevent runaway responses
            }
        }
        response = requests.post(url, json=data, timeout=120)
        response.raise_for_status()
        return response.json()['message']['content']

    @staticmethod
    def _call_claude(provider, messages):
        # Very simplified Claude API call
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        # Claude expects system prompt separately or as a specific message structure
        system = messages[0]['content']
        actual_messages = messages[1:]
        
        data = {
            "model": provider.default_model or "claude-3-haiku-20240307",
            "system": system,
            "messages": actual_messages,
            "max_tokens": 1024
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()['content'][0]['text']

    @staticmethod
    def _call_gemini(provider, messages):
        # Simplified Gemini API call
        api_key = provider.api_key
        model = provider.default_model or "gemini-1.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        
        # Format messages for Gemini
        contents = []
        for m in messages:
            role = "user" if m['role'] in ['user', 'system'] else "model"
            contents.append({"role": role, "parts": [{"text": m['content']}]})
            
        data = {"contents": contents}
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        return response.json()['candidates'][0]['content']['parts'][0]['text']
