from abc import abstractmethod
from typing import Callable, Any
import json
from ..handler import Handler
from ...utility.media import extract_image 
from ...utility.strings import extract_json

class LLMHandler(Handler):
    """Every LLM model handler should extend this class."""
    history = []
    prompts = []
    schema_key = "llm-settings"

    def __init__(self, settings, path):
        self.settings = settings
        self.path = path

    def set_secondary_settings(self, secondary: bool):
        """Set the secondary settings for the LLM"""
        if secondary:
            self.schema_key = "llm-secondary-settings"
        else:
            self.schema_key = "llm-settings"

    def is_secondary(self) -> bool:
        """ Return if the LLM is a secondary one"""
        return self.schema_key == "llm-secondary-settings"

    def supports_vision(self) -> bool:
        """ Return if the LLM supports receiving images"""
        return False

    def supports_video_vision(self) -> bool:
        """ Return if the LLM supports receiving videos"""
        return False
    def get_supported_files(self) -> list[str]:
        """ Return the list of supported files excluding the vision ones"""
        return []

    def stream_enabled(self) -> bool:
        """ Return if the LLM supports token streaming"""
        enabled = self.get_setting("streaming")
        if enabled is None:
            return False
        return enabled

    def load_model(self, model):
        """ Load the specified model """
        return True

    def set_history(self, prompts : list[str], history: list[dict[str, str]]):
        """Set the current history and prompts

        Args:
            prompts (list[str]): list of sytem prompts
            window : Application window
        """        
        self.prompts = prompts
        self.history = history

    @abstractmethod
    def generate_text(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = []) -> str:
        """Generate test from the given prompt, history and system prompt

        Args:
            prompt (str): text of the prompt
            history (dict[str, str], optional): history of the chat. Defaults to {}.
            system_prompt (list[str], optional): content of the system prompt. Defaults to [].

        Returns:
            str: generated text
        """        
        pass

    @abstractmethod
    def generate_text_stream(self, prompt: str, history: list[dict[str, str]] = [], system_prompt: list[str] = [], on_update: Callable[[str], Any] = lambda _: None, extra_args : list = []) -> str:
        """_summary_

        Args:
            prompt (str): text of the prompt
            history (dict[str, str], optional): history of the chat. Defaults to {}.
            system_prompt (list[str], optional): content of the system prompt. Defaults to [].
            on_update (Callable[[str], Any], optional): Function to call when text is generated. The partial message is the first agrument Defaults to ().
            extra_args (list, optional): extra arguments to pass to the on_update function. Defaults to [].
        
        Returns:
            str: generated text
        """  
        pass

    def send_message(self, window, message:str) -> str:
        """Send a message to the bot

        Args:
            window: The window
            message: Text of the message

        Returns:
            str: Response of the bot
        """        
        return self.generate_text(message, self.history, self.prompts)

    def send_message_stream(self, window, message:str, on_update: Callable[[str], Any] = (), extra_args : list = []) -> str:
        """Send a message to the bot

        Args:
            window: The window
            message: Text of the message
            on_update (Callable[[str], Any], optional): Function to call when text is generated. The partial message is the first agrument Defaults to ().
            extra_args (list, optional): extra arguments to pass to the on_update function. Defaults to [].

        Returns:
            str: Response of the bot
        """        
        return self.generate_text_stream(message, self.history, self.prompts, on_update, extra_args)
 
    def get_suggestions(self, request_prompt:str = "", amount:int=1) -> list[str]:
        """Get suggestions for the current chat. The default implementation expects the result as a JSON Array containing the suggestions

        Args:
            request_prompt: The prompt to get the suggestions
            amount: Amount of suggstions to generate

        Returns:
            list[str]: prompt suggestions
        """
        result = []
        max_requests = 3
        req = 0
        history = ""
        # Only get the last four elements and reconstruct partial history
        for message in self.history[-4:] if len(self.history) >= 4 else self.history:
            image, text = extract_image(message["Message"])
            history += message["User"] + ": " + text + "\n"
        for i in range(0, amount):
            if req >= max_requests:
                break
            try:
                req+=1
                generated = self.generate_text(request_prompt + "\n\n" + history)
            except Exception as e:
                continue
            generated = extract_json(generated)
            try:
                j = json.loads(generated)
            except Exception as _:
                continue
            if type(j) is list:
                for suggestion in j:
                    if type(suggestion) is str:
                        result.append(suggestion)
                        i+=1
                        if i >= amount:
                            break
        return result

    def generate_chat_name(self, request_prompt:str = "") -> str | None:
        """Generate name of the current chat

        Args:
            request_prompt (str, optional): Extra prompt to generate the name. Defaults to None.

        Returns:
            str: name of the chat
        """
        try:
             t = self.generate_text(request_prompt, self.history)
             return t
        except Exception as e:          
            print(e)
            return None 


