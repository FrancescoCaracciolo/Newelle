from ..handler import Handler
from abc import abstractmethod
from numpy import ndarray

class EmbeddingHandler(Handler):
    key = ""
    schema_key = "embedding-settings"


    def __init__(self, settings, path):
        super().__init__(settings, path)
        self.dim = None 

    def load_model(self):
        """Load embedding model, called at every settings reload"""
        pass 

    @abstractmethod 
    def get_embedding(self, text: list[str]) -> ndarray:
        """
        Get the embedding for the given text

        Args:
            text: text to embed 

        Returns:
            ndarray: embedding 
        """
        pass

    def get_embedding_size(self) -> int:
        if self.dim is None:
            self.dim = self.get_embedding(["test"]).shape[1]
        return self.dim
