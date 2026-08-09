import os
import sys
import json

class LLMClient:
    def __init__(self, provider: str, model: str, **kwargs):
        self.provider = provider
        self.model = model
        self.kwargs = kwargs

    def generate(self, prompt: str, system: str = None) -> str:
        if self.provider == "gemini":
            return self._generate_gemini(prompt, system)
        elif self.provider == "openai":
            return self._generate_openai(prompt, system)
        elif self.provider == "anthropic":
            return self._generate_anthropic(prompt, system)
        else:
            print(f"Warning: Unknown LLM provider '{self.provider}'. Skipping.", file=sys.stderr)
            return "{}"
            
    def _generate_gemini(self, prompt: str, system: str = None) -> str:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Warning: GEMINI_API_KEY missing. Skipping LLM call.", file=sys.stderr)
            return "{}"
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(self.model, system_instruction=system)
            
            generation_config = {}
            if "temperature" in self.kwargs:
                generation_config["temperature"] = self.kwargs["temperature"]
            if "max_output_tokens" in self.kwargs:
                generation_config["max_output_tokens"] = self.kwargs["max_output_tokens"]
                
            response = model.generate_content(prompt, generation_config=generation_config)
            return response.text
        except ImportError:
            print("Warning: google-generativeai not installed. Skipping LLM call.", file=sys.stderr)
            return "{}"
        except Exception as e:
            print(f"Warning: Gemini API error: {e}. Skipping LLM call.", file=sys.stderr)
            return "{}"

    def _generate_openai(self, prompt: str, system: str = None) -> str:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("Warning: OPENAI_API_KEY missing. Skipping LLM call.", file=sys.stderr)
            return "{}"
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            
            kwargs = {}
            if "temperature" in self.kwargs:
                kwargs["temperature"] = self.kwargs["temperature"]
            if "max_output_tokens" in self.kwargs:
                kwargs["max_tokens"] = self.kwargs["max_output_tokens"]
                
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )
            return response.choices[0].message.content
        except ImportError:
            print("Warning: openai not installed. Skipping LLM call.", file=sys.stderr)
            return "{}"
        except Exception as e:
            print(f"Warning: OpenAI API error: {e}. Skipping LLM call.", file=sys.stderr)
            return "{}"

    def _generate_anthropic(self, prompt: str, system: str = None) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("Warning: ANTHROPIC_API_KEY missing. Skipping LLM call.", file=sys.stderr)
            return "{}"
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            
            kwargs = {}
            if "temperature" in self.kwargs:
                kwargs["temperature"] = self.kwargs["temperature"]
            
            max_tokens = self.kwargs.get("max_output_tokens", 4096)
                
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system or anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": prompt}],
                **kwargs
            )
            return response.content[0].text
        except ImportError:
            print("Warning: anthropic not installed. Skipping LLM call.", file=sys.stderr)
            return "{}"
        except Exception as e:
            print(f"Warning: Anthropic API error: {e}. Skipping LLM call.", file=sys.stderr)
            return "{}"
