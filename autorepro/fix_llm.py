import os
import re

files = ['agent/nodes/analyze.py', 'agent/nodes/generate.py', 'agent/nodes/refine.py']

for f in files:
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    
    pattern = re.compile(r'def _get_llm\(\).*?return ChatOpenAI[^\n]*\n', re.DOTALL)
    
    def replacer(match):
        orig = match.group(0)
        if 'groq' in orig:
            return orig
        temp_match = re.search(r'temperature=([0-9.]+)', orig)
        temp = temp_match.group(1) if temp_match else '0'
        
        new_func = f'''def _get_llm():
    """Return the configured LLM instance."""
    if config.LLM_PROVIDER == "mock":
        from utils.mock_llm import MockLLM
        return MockLLM()
    if config.LLM_PROVIDER == "bedrock":
        from langchain_aws import ChatBedrockConverse
        return ChatBedrockConverse(model=config.LLM_MODEL, temperature={temp})
    if config.LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=config.LLM_MODEL, temperature={temp})
    if config.LLM_PROVIDER == "google" or config.LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=config.LLM_MODEL, temperature={temp})
    if config.LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=config.LLM_MODEL, temperature={temp})
    if config.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=config.LLM_MODEL, temperature={temp})
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=config.LLM_MODEL, temperature={temp})
'''
        return new_func
        
    new_content = pattern.sub(replacer, content)
    with open(f, 'w', encoding='utf-8') as file:
        file.write(new_content)
    print(f'Updated {f}')
