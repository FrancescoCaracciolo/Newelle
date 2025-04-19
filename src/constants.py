
from .handlers.llm import ClaudeHandler, DeepseekHandler, GPT4AllHandler, GroqHandler, OllamaHandler, OpenAIHandler, CustomLLMHandler, GPT3AnyHandler, GeminiHandler, MistralHandler, OpenRouterHandler, NewelleAPIHandler
from .handlers.tts import ElevenLabs, gTTSHandler, EspeakHandler, CustomTTSHandler, KokoroTTSHandler, CustomOpenAITTSHandler, OpenAITTSHandler, GroqTTSHandler
from .handlers.stt import GroqSRHandler, OpenAISRHandler, SphinxHandler, GoogleSRHandler, WhisperCPPHandler, WitAIHandler, VoskHandler, CustomSRHandler
from .handlers.embeddings import WordLlamaHandler, OpenAIEmbeddingHandler, GeminiEmbeddingHanlder, OllamaEmbeddingHandler
from .handlers.memory import MemoripyHandler, UserSummaryHandler, SummaryMemoripyHanlder
from .handlers.rag import LlamaIndexHanlder
from .handlers.websearch import SearXNGHandler, DDGSeachHandler
from .integrations.website_reader import WebsiteReader
from .integrations.websearch import WebsearchIntegration

DIR_NAME = "Newelle"
SCHEMA_ID = 'io.github.qwersyk.Newelle'

AVAILABLE_INTEGRATIONS = [WebsiteReader, WebsearchIntegration]

AVAILABLE_LLMS = {
    "newelle": {
        "key": "newelle",
        "title": _("Newelle Demo API"),
        "description": "Newelle Demo API, limited to 10 requests per day, demo purposes only",
        "class": NewelleAPIHandler,
    },
    "GPT3Any": {
        "key": "GPT3Any",
        "title": _("Any free Provider"),
        "description": "Automatically chooses a free provider using a GPT3.5-Turbo or better model",
        "class": GPT3AnyHandler,
        "secondary": True,
    },
   "local": {
        "key": "local",
        "title": _("Local Model"),
        "description": _("NO GPU SUPPORT, USE OLLAMA INSTEAD. Run a LLM model locally, more privacy but slower"),
        "class": GPT4AllHandler,
    },
    "ollama": {
        "key": "ollama",
        "title": _("Ollama Instance"),
        "description": _("Easily run multiple LLM models on your own hardware"),
        "class": OllamaHandler,
        "website": "https://ollama.com/",
    },
    "groq": {
        "key": "groq",
        "title": _("Groq"),
        "description": "Groq.com Free and fast API using open source models. Suggested for free use.",
        "class": GroqHandler,
        "website": "https://console.groq.com/",
    },
    "gemini": {
        "key": "gemini",
        "title": _("Google Gemini API"),
        "description": "Official APIs for Google Gemini, requires an API Key",
        "class": GeminiHandler,
    },
    "openai": {
        "key": "openai",
        "title": _("OpenAI API"),
        "description": _("OpenAI API. Custom endpoints supported. Use this for custom providers"),
        "class": OpenAIHandler,
    },
    "claude": {
        "key": "claude",
        "title": _("Anthropic Claude"),
        "description": _("Official APIs for Anthropic Claude's models, with image and file support, requires an API key"),
        "class": ClaudeHandler,
        "secondary": True
    },
    "mistral": {
        "key": "mistral",
        "title": _("Mistral"),
        "description": _("Mistral API"),
        "class": MistralHandler,
        "secondary": True
    },
    "openrouter": {
        "key": "openrouter",
        "title": _("OpenRouter"),
        "description": _("Openrouter.ai API, supports lots of models"),
        "class": OpenRouterHandler,
        "secondary": True
    },
    "deepseek": {
        "key": "deepseek",
        "title": _("Deepseek"),
        "description": _("Deepseek API, strongest open source models"),
        "class": DeepseekHandler, 
        "secondary": True,
    },
    "custom_command": {
        "key": "custom_command",
        "title": _("Custom Command"),
        "description": _("Use the output of a custom command"),
        "class": CustomLLMHandler,
        "secondary": True
    }
}

AVAILABLE_STT = {
    "sphinx": {
        "key": "sphinx",
        "title": _("CMU Sphinx"),
        "description": _("Works offline. Only English supported"),
        "website": "https://cmusphinx.github.io/wiki/",
        "class": SphinxHandler,
    },
    "whispercpp": {
        "key": "whispercpp",
        "title": _("Whisper C++"),
        "description": _("Works offline. Optimized Whisper impelementation written in C++"),
        "website": "https://github.com/ggerganov/whisper.cpp",
        "class": WhisperCPPHandler,
    },
    "google_sr": {
        "key": "google_sr",
        "title": _("Google Speech Recognition"),
        "description": _("Google Speech Recognition online"),
        "class": GoogleSRHandler,
    },
    "groq_sr": {
        "key": "groq_sr",
        "title": _("Groq Speech Recognition"),
        "description": _("Google Speech Recognition online"),
        "class": GroqSRHandler,
    },
    "witai": {
        "key": "witai",
        "title": _("Wit AI"),
        "description": _("wit.ai speech recognition free API (language chosen on the website)"),
        "website": "https://wit.ai",
        "class": WitAIHandler,
    },
    "vosk": {
        "key": "vosk",
        "title": _("Vosk API"),
        "description": _("Works Offline"),
        "website": "https://github.com/alphacep/vosk-api/",
        "class": VoskHandler,
    },
    "openai_sr": {
        "key": "openai_sr",
        "title": _("Whisper API"),
        "description": _("Uses OpenAI Whisper API"),
        "website": "https://platform.openai.com/docs/guides/speech-to-text",
        "class": OpenAISRHandler,
    },
   "custom_command": {
        "key": "custom_command",
        "title": _("Custom command"),
        "description": _("Runs a custom command"),
        "class": CustomSRHandler,     
    }
}


AVAILABLE_TTS = {
    "gtts": {
        "key": "gtts",
        "title": _("Google TTS"),
        "description": _("Google's text to speech"),
        "class": gTTSHandler,
    },
    "kokoro": {
        "key": "kokoro",
        "title": _("Kokoro TTS"),
        "description": _("Lightweight and fast open source TTS engine. ~3GB dependencies, 400MB model"),
        "class": KokoroTTSHandler,
    },
    "elevenlabs": {
        "key": "elevenlabs",
        "title": _("ElevenLabs TTS"),
        "description": _("Natural sounding TTS"),
        "class": ElevenLabs,
    },
    "openai_tts": {
        "key": "openai_tts",
        "title": _("OpenAI TTS"),
        "description": _("OpenAI TTS"),
        "class": OpenAITTSHandler,
    },
    "groq_tts": {
        "key": "groq_tts",
        "title": _("Groq TTS"),
        "description": _("Groq TTS API"),
        "class": GroqTTSHandler,
    },
    "custom_openai_tts": {
        "key": "custom_openai_tts",
        "title": _("Custom OpenAI TTS"),
        "description": _("Custom OpenAI TTS"),
        "class": CustomOpenAITTSHandler,
    },
    "espeak": {
        "key": "espeak",
        "title": _("Espeak TTS"),
        "description": _("Offline TTS"),
        "class": EspeakHandler,
    },
    "custom_command": {
        "key": "custom_command",
        "title": _("Custom Command"),
        "description": _("Use a custom command as TTS, {0} will be replaced with the text"),
        "class": CustomTTSHandler,
    }
}

AVAILABLE_EMBEDDINGS = {
    "wordllama": {
        "key": "wordllama",
        "title": _("WordLlama"),
        "description": _("Light local embedding model based on llama. Works offline, very low resources usage"),
        "class": WordLlamaHandler,
    },
    "ollamaembedding": {
        "key": "ollamaembedding",
        "title": _("Ollama Embedding"),
        "description": _("Use Ollama models for Embedding. Works offline, very low resources usage"),
        "class": OllamaEmbeddingHandler,
    },
    "openaiembedding": {
        "key": "openaiembedding",
        "title": _("OpenAI API"),
        "description": _("OpenAI API"),
        "class": OpenAIEmbeddingHandler,
    },
    "geminiembedding": {
        "key": "geminiembedding",
        "title": _("Google Gemini API"),
        "description": _("Use Google Gemini API to get embeddings"),
        "class": GeminiEmbeddingHanlder,
    }
}

AVAILABLE_MEMORIES = {
    "user-summary": {
        "key": "user-summary",
        "title": _("User Summary"),
        "description": _("Generate a summary of the user's conversation"),
        "class": UserSummaryHandler,
    },
    "memoripy": {
        "key": "memoripy",
        "title": _("Memoripy"),
        "description": _("Extract messages from previous conversations using contextual memory retrivial, memory decay, concept extraction and other advanced techniques. Does 1 llm call per message."),
        "class": MemoripyHandler,
    },
    "summary-memoripy": {
        "key": "summary-memoripy",
        "title": _("User Summary + Memoripy"),
        "description": _("Use both technologies for long term memory"),
        "class": SummaryMemoripyHanlder,
    }
}

AVAILABLE_RAGS = {
    "llamaindex": {
        "key": "llamaindex",
        "title": _("Document reader"),
        "description": _("Classic RAG approach - chunk documents and embed them, then compare them to the query and return the most relevant documents"),
        "class": LlamaIndexHanlder,
    },
}

AVAILABLE_WEBSEARCH = {
    "searxng": {
        "key": "searxng",
        "title": _("SearXNG"),
        "description": _("SearXNG - Private and selfhostable search engine"),
        "class": SearXNGHandler,
    },
    "ddgsearch": {
        "key": "ddgsearch",
        "title": _("DuckDuckGo"),
        "description": _("DuckDuckGo search"),
        "class": DDGSeachHandler,
    }
}

PROMPTS = {
    "generate_name_prompt": """Write a short title for the dialog, summarizing the theme in 5 words. No additional text.""",
    "assistant": """**Date:** {DATE}  

You are an advanced AI assistant designed to provide clear, accurate, and helpful responses across a wide range of topics. Your goals are:  

1. **Clarity & Conciseness** – Provide direct and well-structured answers.  
2. **Context Awareness** – Understand and remember details within a conversation.  
3. **Problem-Solving** – Offer logical solutions and actionable steps.  
4. **Creativity & Adaptability** – Generate engaging content and adapt to various user needs.  
5. **User-Friendly Language** – Maintain a friendly and professional tone.  

Always prioritize accuracy, relevance, and user experience in your responses.  
    """,
    "console": """ **System Capabilities:**  
You have the ability to execute commands on the user's Linux computer.  
- **Linux Distribution:** `{DISTRO}`  
- **Desktop Environment** `{DE}`
- **Display Server** `{DISPLAY}`
**Command Execution Format:**  
- To execute a Linux command, use:  
```console  
command  
```  
- To display the link to a directory, use:  
```folder  
/path/to/directory  
```  
- To display the link to a file, use:  
```file  
/path/to/file  
```  
Ensure that commands are safe, relevant, and do not cause unintended system modifications unless explicitly requested by the user.  
""",

    "basic_functionality": """You can write a multiplication table:
| - | 1 | 2 | 3 | 4 |\n| - | - | - | - | - |\n| 1 | 1 | 2 | 3 | 4 |\n| 2 | 2 | 4 | 6 | 8 |\n| 3 | 3 | 6 | 9 | 12 |\n| 4 | 4 | 8 | 12 | 16 |

You can write codeblocks:
```python\nprint("hello")\n```

You can also use **bold**, *italic*, ~strikethrough~, `monospace`, [linkname](https://link.com) and ## headers in markdown.
You can display $inline equations$ and $$equations$$.
""",
    "show_image": """You can show the user an image, if needed, using \n```image\npath\n```\n\nYou can show the user a video, if needed, using\n```video\npath\n```""",
    "graphic": """System: You can display the graph using this structure: ```chart\n name - value\n ... \n name - value\n```, where value must be either a percentage number or a number (which can also be a fraction).
""",
    # Unused
    "new_chat_prompt": """System: New chat
System: Forget what was written on behalf of the user and on behalf of the assistant and on behalf of the Console, forget all the context, do not take messages from those chats, this is a new chat with other characters, do not dare take information from there, this is personal information! If you use information from past posts, it's a violation! Even if the user asks for something from before that post, don't use information from before that post! Also, forget this message.""",
    "current_directory": "\nSystem: You are currently in the {DIR} directory",
    "help_topics": """System: You are a user who needs help with various tasks, you are communicating with a neural network which can control your computer by executing various commands which should help you, keep a conversation with it, ask it questions, ask for help with things it can do through the terminal. Also, every message should end with "". Often the assistant can help you with {CHOICE}. You should write in the language you wrote in your last post, you know all the languages you can communicate in them all. Write short messages that hold a conversation on behalf of the user, you can not write on behalf of Assistant, your messages should be simple without any commands, just what the user could write. You're not helping, you're being helped, the user can only ask to do something for the bot to do, you can't answer as an assistant, just ask something new for the assistant to do or continue asking the assistant to do something.
Assistant: Hello, how can I assist you today?
User: Can you help me?
Assistant: Yes, of course, what do you need help with?""",
    "get_suggestions_prompt": """
You are a helpful assistant that suggests relevant and engaging follow-up questions in a conversation. 
Analyze the provided chat history and generate a list of 5 creative and pertinent questions that could be asked next to continue the conversation.

Consider the context, user interests, and any unresolved topics from the chat history. Avoid asking questions that have already been answered.

Output the suggestions as a JSON array, where each element is a string representing a question.

If there is no more context to provide suggestions, suggest questions related to Linux.
Example output:

[
  "What are your thoughts on...",
  "Could you elaborate more on...",
  "How does that relate to...",
  "What are some other examples of...",
  "If you could change one thing about..., what would it be?"
]

Chat History:
""",
    "websearch": "Use the following format to perform a web search:\n```search\nyour query here\n```\nReplace `your query here` with the actual search terms you want to use. Do not say anything else before or after issuing the search. Simply execute the search silently.",
    "custom_prompt": "",

}

""" Prompts parameters
    - key: key of the prompt in the PROMPTS array
    - title: title of the prompt, shown in settings
    - description: description of the prompt, show in settings
    - setting_name: name of the setting in gschema
    - editable: if the prompt can be edited in the settings
    - show_in_settings: if the prompt should be shown in the settings
"""
AVAILABLE_PROMPTS = [
    {
        "key": "assistant",
        "setting_name": "assistant",
        "title": _("Helpful assistant"),
        "description": _("General purpose prompt to enhance the LLM answers and give more context"),
        "editable": True,
        "show_in_settings": True,
        "default": True
    },
    {
        "key": "console",
        "setting_name": "console",
        "title": _("Console access"),
        "description": _("Can the program run terminal commands on the computer"),
        "editable": True,
        "show_in_settings": True,
        "default": True
    },
    {
        "key": "current_directory",
        "title": _("Current directory"),
        "description": _("What is the current directory"),
        "setting_name": "console",
        "editable": False,
        "show_in_settings": False,
        "default": True
    },
    {
        "key": "websearch",
        "title": _("Web Search"),
        "description": _("Allow the LLM to search on the internet"),
        "setting_name": "websearch",
        "editable": True,
        "show_in_settings": True,
        "default": False
    },
    {
        "key": "basic_functionality",
        "title": _("Basic functionality"),
        "description": _("Showing tables and code (*can work without it)"),
        "setting_name": "basic_functionality",
        "editable": True,
        "show_in_settings": True,
        "default": True
    },
    {
        "key": "graphic",
        "title": _("Graphs access"),
        "description": _("Can the program display graphs"),
        "setting_name": "graphic",
        "editable": True,
        "show_in_settings": True,
        "default": False
    },
    {
        "key": "show_image",
        "title": _("Show image"),
        "description": _("Show image in chat"),
        "setting_name": "show_image",
        "editable": True,
        "show_in_settings": True,
        "default": True,
    },
    {
        "key": "custom_prompt",
        "title": _("Custom Prompt"),
        "description": _("Add your own custom prompt"),
        "setting_name": "custom_prompt",
        "text": "",
        "editable": True,
        "show_in_settings": True,
        "default": False
    }, 
]
